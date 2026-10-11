"""Intent catalog: exemplar utterances (the labelled NL corpus the router retrieves over) plus high-precision cues.

Exemplars use slot placeholders ({product}, {period}, {num}...) that are matched against the *delexicalised* user
utterance, so "create a PO for 500 Steel Rod from Global Metals" and "...300 Ball Bearing from VoltEdge" look identical
to the router. Cues add evidence for intents with unmistakable wording; they never override a clear retrieval winner on
their own (scores are additive).
"""
import re
from collections import defaultdict

PLACEHOLDERS = {"{product}": "entproduct", "{supplier}": "entsupplier", "{customer}": "entcustomer", "{category}": "entcategory",
                "{region}": "entregion", "{warehouse}": "entwarehouse", "{period}": "periodtok", "{num}": "numtok",
                "{po}": "poreftok", "{so}": "soreftok", "{money}": "moneytok"}

INTENTS = {
    "show_view": {"risk": "read", "desc": "Navigate to a page and set its filters / date range / sort.", "examples": [
        "go to the inventory page", "take me to suppliers", "open procurement", "show me the sales orders", "navigate to finance",
        "bring up the logistics page", "open the dashboard", "show low stock items", "filter orders to delivered only", "show cancelled orders for {period}",
        "show suppliers in {country}", "list pending approvals", "open analytics", "show {region} sales for {period}", "sort inventory by days left",
        "show delayed shipments", "show draft purchase orders", "show orders from {region}", "show {warehouse} warehouse stock", "show approved POs for {supplier}",
        "open the sales page filtered to {category}", "only show out of stock products", "switch to the finance page for {period}", "view shipments in transit",
        "show me all {category} products", "pull up {supplier} purchase orders", "show stock of {product}", "display orders from {customer}", "show suppliers rated above 4",
        "open inventory and suppliers in new tabs", "open procurement in a new tab", "show purchase orders for {period} sorted by amount",
        "show on hold suppliers", "list delivered orders in {region}"]},
    "explain_page": {"risk": "read", "desc": "Describe the page the user is currently on.", "examples": [
        "what page am I on", "what is this page", "explain this dashboard", "what can I do here", "what does this screen show",
        "which filters are available here", "describe the current page", "where am I", "tell me about this page", "what am I looking at",
        "what are the widgets on this page", "what is on this screen"]},
    "help": {"risk": "read", "desc": "Explain what the assistant can do.", "examples": [
        "what can you do", "help", "how can you help me", "what are your capabilities", "how do I use you", "what kind of things can I ask you",
        "give me some examples of what to ask", "what do you do"]},
    "reset_filters": {"risk": "read", "desc": "Clear all filters on the current page.", "examples": [
        "clear all filters", "reset the filters", "remove all filters", "start over with the default view", "reset this page", "clear the selection",
        "show everything again", "undo the filters"]},
    "unsupported": {"risk": "read", "desc": "Requests for actions the assistant deliberately cannot perform (email, payments, wiping data).", "examples": [
        "send an email to the supplier", "email the team the report", "transfer money to a vendor", "pay this invoice", "wipe the database", "delete all customers",
        "drop the orders table", "post this on slack", "book a flight", "call the carrier"]},
    "chitchat": {"risk": "read", "desc": "Greetings and thanks.", "examples": ["hi", "hello", "hey there", "thanks", "thank you", "good morning", "ok great", "cool thanks"]},
    "kpi_query": {"risk": "read", "desc": "Answer a single business number for a period.", "examples": [
        "what is total revenue for {period}", "how many orders in {period}", "what's the inventory value", "how many active suppliers do we have",
        "what was the profit in {period}", "total expenses {period}", "what is our on time delivery rate", "how much do customers owe us in receivables",
        "cancellation rate in {period}", "average order value for {region}", "how much did we spend on procurement {period}", "how many units did we sell {period}",
        "what are the sales for {category} in {period}", "tell me the revenue from {region}", "number of delayed shipments {period}", "what are our current receivables"]},
    "trend": {"risk": "read", "desc": "Show how a metric evolves over time with insights.", "examples": [
        "show revenue trend for {period}", "analyze the sales trend for {period}", "how has profit been trending", "monthly orders over {period}",
        "revenue over time by month", "how are expenses evolving", "plot units sold trend", "trend of on time delivery", "give me the revenue trend",
        "show me the growth trajectory of sales", "how did orders change over {period}", "analyze {category} sales trend", "revenue history for {region}"]},
    "compare_periods": {"risk": "read", "desc": "Compare a metric between two periods.", "examples": [
        "compare revenue {period} vs {period}", "{period} vs {period} orders", "how does {period} compare with {period}", "revenue growth versus previous quarter",
        "compare profit this quarter against last quarter", "compare the last 6 months with the previous 6 months", "how are sales doing versus last year",
        "orders this month vs last month", "compare {region} revenue {period} against {period}", "what is the change in expenses compared to last month",
        "period over period comparison of revenue", "revenue compared to the previous period"]},
    "explain_change": {"risk": "read", "desc": "Explain why a metric changed.", "examples": [
        "why did revenue drop in {period}", "what caused the decline in sales last month", "explain the dip in orders", "reasons for the increase in expenses",
        "why is profit lower this quarter", "what happened to {category} sales in {period}", "what is driving the change in cancellation rate",
        "why were orders down in {period}", "what explains the revenue spike in {period}", "find the root cause of the sales decrease",
        "why did on time delivery fall", "how come revenue is lower than last month"]},
    "top_n": {"risk": "read", "desc": "Rank entities (products, customers, suppliers, regions, categories) by a metric.", "examples": [
        "top {num} products by revenue", "best selling products {period}", "which customers spend the most", "worst performing regions",
        "top {num} suppliers by spend", "bottom {num} products by units sold", "highest revenue categories this year", "biggest customers in {region}",
        "who are our top customers", "rank regions by sales", "best performing categories", "least selling products {period}", "top {num} {category} products"]},
    "stockout_forecast": {"risk": "read", "desc": "Forecast which products will run out of stock.", "examples": [
        "which products are likely to run out in {num} days", "what will go out of stock next week", "items that need reorder soon", "show stock out risk",
        "which materials will be depleted within a month", "what is running low", "forecast stockouts for the next {num} days", "what is about to run out",
        "products at risk of stockout", "what do we need to reorder soon", "which items will we run out of soon", "show shortage risk for next {num} days"]},
    "compare_suppliers": {"risk": "read", "desc": "Rank / compare suppliers for a product.", "examples": [
        "compare top {num} suppliers for {product} with best pricing", "who is the cheapest supplier for {product}", "best vendor for {product}",
        "rank suppliers for {product} by lead time", "which supplier offers the best price for {product}", "compare vendors for {product}",
        "find me the most reliable supplier of {product}", "who should we buy {product} from", "supplier comparison for {product}", "cheapest source for {product}", "who supplies {product}", "which suppliers sell {product}"]},
    "entity_lookup": {"risk": "read", "desc": "Show details of a supplier, product, customer, PO or sales order.", "examples": [
        "tell me about {supplier}", "details for {product}", "status of {po}", "where is order {so}", "show me info on {customer}", "look up {supplier}",
        "what is the stock of {product}", "give me the details of {po}", "profile of {customer}", "what is the status of {so}",
        "how is {supplier} performing", "information about {product}"]},
    "generate_report": {"risk": "read", "desc": "Generate an executive business report.", "examples": [
        "generate a monthly business report for {period}", "give me an executive summary for {period}", "prepare a weekly summary", "write a business review for {period}",
        "create a report on procurement for {period}", "summarize the business for {period}", "I need a management report", "business snapshot for {period}",
        "compile a performance report", "give me a recap of how we did in {period}", "executive brief please"]},
    "create_po": {"risk": "write", "desc": "Create a purchase order for a specific product (form pre-fill + confirmation).", "examples": [
        "create a purchase order for {num} {product} from {supplier}", "order {num} {product}", "raise a PO for {product}", "buy {num} {product} from the cheapest supplier",
        "place an order for {num} {product} with {supplier}", "new purchase order for {product}", "I need to order {num} units of {product}", "make a PO for {num} {product}",
        "purchase {num} {product} from {supplier}", "reorder {product} {num} units"]},
    "create_po_bulk": {"risk": "write", "desc": "Plan and draft purchase orders for all products that are low / about to run out, choosing the best supplier.", "examples": [
        "create purchase orders for all low stock items", "analyze inventory items that may run out in the next {num} days and create purchase order drafts with best supplier options",
        "restock everything that is running low", "draft POs for items below reorder level", "prepare replenishment orders for products running out soon",
        "auto reorder low stock products", "create purchase orders for products that will run out soon", "raise POs for everything we need to replenish",
        "generate purchase order drafts for the shortage items", "create po drafts for out of stock items using the best suppliers"]},
    "submit_po": {"risk": "write", "desc": "Submit draft purchase orders (auto-approve up to $50k, otherwise route to approvals).", "examples": [
        "submit all draft purchase orders", "submit {po}", "send the drafts for approval", "release my draft POs", "submit the draft orders", "push {po} through for approval"]},
    "approve_po": {"risk": "write", "desc": "Approve or reject purchase orders waiting for approval.", "examples": [
        "approve all pending purchase orders under {money}", "approve {po}", "approve the pending approvals", "reject {po}", "approve everything waiting for me",
        "sign off on pending POs", "approve pending purchase orders", "decline {po}", "approve all pending approvals below my limit"]},
    "receive_po": {"risk": "write", "desc": "Mark a purchase order as received and add stock.", "examples": [
        "mark {po} as received", "receive {po} into {warehouse}", "goods received for {po}", "we received the shipment for {po}", "book in {po}", "log {po} as delivered to {warehouse}"]},
    "delete_po": {"risk": "destructive", "desc": "Delete draft purchase orders.", "examples": [
        "delete draft {po}", "remove the draft purchase order {po}", "discard all draft POs", "delete {po}", "get rid of the draft purchase orders", "scrap {po}"]},
    "update_inventory": {"risk": "write", "desc": "Change stock quantity or reorder level of a product.", "examples": [
        "set the reorder level of {product} to {num}", "adjust {product} stock in {warehouse} to {num}", "update stock of {product} at {warehouse} to {num}",
        "change reorder level for {product} to {num}", "correct the {product} quantity in {warehouse} to {num}", "set {product} inventory to {num} units in {warehouse}",
        "increase the reorder point of {product} to {num}"]},
    "update_order_status": {"risk": "write", "desc": "Move a sales order to a new status.", "examples": [
        "mark order {so} as shipped", "cancel order {so}", "set {so} to delivered", "move order {so} to processing", "update {so} status to shipped", "change {so} to cancelled"]},
    "add_supplier": {"risk": "write", "desc": "Register a new supplier (form pre-fill).", "examples": [
        "add a new supplier Kestrel Alloys from Vietnam for metals", "register supplier Helix Polymers in Germany", "onboard a new vendor", "create a supplier",
        "add supplier called BrightPack from {country} for packaging", "set up a new vendor for {category}"]},
    "update_supplier_status": {"risk": "write", "desc": "Put a supplier on hold, deactivate or reactivate it.", "examples": [
        "put {supplier} on hold", "deactivate {supplier}", "reactivate {supplier}", "set {supplier} to inactive", "suspend supplier {supplier}", "mark {supplier} as active again"]},
}

GREETING = re.compile(r"^(hi|hello|hey|thanks|thank you|thx|good (morning|afternoon|evening)|ok|okay|cool|great|nice)\b[\s\w!.,]*$")


def expand_examples():
    out = []
    for iid, d in INTENTS.items():
        for ex in d["examples"]:
            for ph, tok in PLACEHOLDERS.items():
                ex = ex.replace(ph, tok)
            ex = ex.replace("{country}", "entcountry")
            out.append((iid, ex))
    return out


def boosts(p, ctx) -> dict:
    """Additive evidence per intent from unmistakable wording + slots."""
    t = p.t
    b = defaultdict(float)
    n_words = len(t.split())
    has_po_word = p.rx(r"\bpos?\b|purchase orders?|\bdrafts?\b|replenishment orders?")
    low_phrase = p.rx(r"low[- ]stock|run(?:ning|s)? (?:out|low)|shortage|below (?:the )?reorder|reorder (?:level|point)|replenish|restock|re-?stock|out[- ]of[- ]stock|depleted|at[- ]risk|about to run|everything we need|running low|that need|all (?:the )?(?:low|items|products)")
    if p.rx(r"reorder (?:level|point)") and (p.ents.get("product") or p.product_group) and p.numbers:
        low_phrase = False                      # "set reorder point of X to 600" is an inventory edit, not a replenishment request
    has_prod = bool(p.ents.get("product") or p.product_group)
    has_po_word = has_po_word or False
    nav_verb = p.rx(r"\b(go to|take me|navigate|open|show|display|bring up|switch to|jump to|view|list|pull up|see|filter|sort|only|visit|launch)\b")

    destructive_master = p.rx(r"\b(delete|remove|wipe|drop|truncate|erase|purge|destroy|nuke)\b.{0,25}\b(all|every|entire|whole)\b.{0,25}\b(customers?|suppliers?|products?|inventory|records?|data|tables?|database|orders?)\b") and not has_po_word
    if (p.rx(r"\b(e-?mail|whatsapp|slack|tweet|sms|text message|phone|call)\b") and not p.rx(r"\bcall(?:ed)? (?:it|this)\b")) \
            or p.rx(r"\b(transfer|wire|refund|charge)\b.{0,30}(\$|dollars?|money|bank|account|invoice|payment)|\bpay\b.{0,25}(invoice|supplier|vendor|bill|\$|dollars?)|\b(refund|chargeback|reimburse)\b|\b(wipe|truncate|purge|nuke)\b|\bdrop (?:the |all )?(?:database|table|orders|data)\b|\bbook (?:me )?a\b|\bpublish\b|\bpost (?:this|it)\b") \
            or destructive_master:
        b["unsupported"] += 2.6
    if GREETING.match(t) and n_words <= 4:
        b["chitchat"] += 1.8
    if p.rx(r"what can you do|what do you do|what (?:are|can|could) you (?:able|capable)|able to do|capable of|how can you help|your capabilities|how (?:do|can) i use you|what (?:can|should|could) i ask|^help\b|examples of what|what do you offer"):
        b["help"] += 1.7
    if p.rx(r"(?:what|which) (?:page|screen|view|dashboard)\b|\bthis (?:page|screen|dashboard|view)\b(?!.*\b(?:in|into)\b.*\btab)|current (?:page|screen|view)|where am i|what (?:can|do) i (?:do|see) here|what am i looking at"):
        b["explain_page"] += 1.7
    if p.rx(r"\b(clear|reset|remove|undo)\b.*\b(filters?|selection|view)\b|\bstart over\b|default view|\breset\b|\bclear (?:everything|all)\b"):
        b["reset_filters"] += 1.9
    if p.rx(r"\b(delete|remove|discard|erase|scrap|get rid of)\b") and (p.po_refs or has_po_word):
        b["delete_po"] += 1.7
    if p.rx(r"\b(approve|reject|decline|deny|turn down|veto|sign off|sign-off)\b") and (p.po_refs or p.rx(r"purchase|\bpos?\b|approvals?|pending|waiting|everything|\ball\b")):
        b["approve_po"] += 1.7
    if p.rx(r"\b(submit|release|send (?:the |my |all )?(?:drafts?|pos?|purchase orders?)|send .* for approval|push)\b") and (p.po_refs or p.rx(r"drafts?|\bpos?\b|purchase|orders?")):
        b["submit_po"] += 1.7
    if p.rx(r"\b(receive|received|receipt|book in|log|goods in|arrived)\b") and (p.po_refs or has_po_word) and p.rx(r"\b(mark|receive|received|book|log|goods|confirm|arrived|came)\b") and not nav_verb:
        b["receive_po"] += 1.7
    if p.rx(r"\b(create|raise|place|prepare|generate|make|draft|order|buy|purchase|restock|replenish|reorder|auto)\b") and (has_po_word or p.rx(r"restock|replenish|reorder|auto")) and low_phrase:
        b["create_po_bulk"] += 1.8
    if p.rx(r"analy[sz]e.*(run out|low stock|reorder).*(create|draft|raise|prepare)"):
        b["create_po_bulk"] += 1.0
    if p.rx(r"\b(create|raise|place|new|buy|order|purchase|make|reorder|need|want|require|get us|get me|procure)\b") and has_prod and not low_phrase and not p.rx(r"\b(delete|show|list|submit|approve|status|reorder (?:level|point))\b"):
        b["create_po"] += 1.6 if (has_po_word or p.numbers or p.rx(r"\bbuy\b|\border\b")) else 0.6
        if p.numbers:
            b["compare_suppliers"] -= 1.2      # a quantity means "order it", not "compare vendors"
    if has_po_word and has_prod and p.numbers and not low_phrase and not p.rx(r"\b(delete|show|list|submit|approve|status|reject|receive|received|mark)\b") and p.rx(r"\bfor\b.{0,12}(?:\d|numtok)|\bfrom\b"):
        b["create_po"] += 1.5
    if p.rx(r"\b(set|adjust|change|update|correct|modify|edit|fix|increase|reduce|lower|raise|put|make|bump)\b") and p.rx(r"reorder (?:level|point)|\bstock\b|quantity|\bqty\b|inventory|on hand|units") and has_prod and p.numbers and not has_po_word:
        b["update_inventory"] += 1.7
    if p.so_refs and p.rx(r"\b(mark|set|change|update|move|cancel|ship|deliver|process|switch|flip|push|advance|put)\b"):
        b["update_order_status"] += 1.8
    if p.rx(r"\b(add|register|onboard|create|new|set up)\b.*\b(supplier|vendor)\b") and not p.ents.get("supplier"):
        b["add_supplier"] += 1.8
    if p.ents.get("supplier") and p.rx(r"\b(hold|deactivate|reactivate|activate|suspend|inactive|resume|enable|disable|blacklist|freeze)\b") and p.rx(r"\b(put|set|mark|move|deactivate|reactivate|activate|suspend|resume|enable|disable|place|blacklist|freeze)\b"):
        b["update_supplier_status"] += 1.7
    if p.rx(r"\b(report|summary|summari[sz]e|review|brief|digest|recap|snapshot)\b") and p.rx(r"\b(generate|prepare|give|write|create|produce|build|compile|need|want|draft)\b|business|executive|monthly|weekly|quarterly|management|performance") \
            and not p.rx(r"trend|compare|why"):
        b["generate_report"] += 1.5
    if p.rx(r"\bwhy\b|what (?:caused|happened|drove|is driving|explains|led)|reasons? for|root cause|explain (?:the|why)|what.s behind|how come|driving the|what.s driving") and (p.metric or p.rx(r"\b(drop|fall|fell|decline|dip|rise|spike|increase|decrease|lower|higher|down|up)\b")):
        b["explain_change"] += 1.6
    if p.rx(r"\bcompare|compared|\bvs\b|versus|against|growth|change from|difference between|how does .* compare|period over period|\bmom\b|\byoy\b") and not (p.rx(r"suppliers?|vendors?") and has_prod):
        b["compare_periods"] += 0.9 + (0.6 if len(p.periods) >= 2 else 0) + (0.3 if p.metric else 0)
    if has_prod and p.rx(r"\b(?:best|cheapest|lowest|better) (?:price|deal|rate|cost)|\bcheapest\b|\bwho (?:has|sells|offers|supplies|stocks|makes)\b") and not p.rx(r"\b(create|raise|place|buy|order)\b") \
            and not [v for v, *_ in p.numbers if v != p.top_n and v != p.horizon_days]:       # a quantity means "order it"
        b["compare_suppliers"] += 1.6
    if p.rx(r"suppliers?|vendors?|sources?|buy .* from|supplies|\bsells?\b|sellers?|\bprovides?\b|\boffers?\b") and has_prod and p.rx(r"\b(compare|best|cheapest|rank|cheap|price|pricing|reliable|fastest|who|which|offers?|should we)\b") and not p.rx(r"\b(create|raise|place)\b.*\b(po|purchase)"):
        b["compare_suppliers"] += 1.8
        b["compare_periods"] -= 1.0
    if p.rx(r"\btrends?\b|over time|month by month|monthly|evolv|trajectory|history of|how has .*(?:been )?(?:trending|changed|performed|doing)|time series|\bplot\b|how did .* (?:change|develop|progress|evolve)|\bdevelop(?:ed|ing)?\b|\bprogress(?:ed|ing)?\b|over the (?:last|past) \d+ (?:months?|weeks?|days?|years?)") and not p.rx(r"\bwhy\b"):
        b["trend"] += 1.1 + (0.3 if p.metric else 0)
    if (p.top_n or p.rx(r"\b(top|best|worst|bottom|highest|lowest|biggest|largest|leading|most|least|rank|weakest|poorest|strongest|smallest)\b")) and p.entity_noun \
            and not p.rx(r"low[- ]stock|run out|\bstock\b|delay|shipments?|\bcompare\b.*\bvs\b|\bwho (?:sells|supplies)|cheapest|\bprice\b|pricing") and not (has_prod and p.entity_noun == "supplier" and not p.top_n):
        b["top_n"] += 1.5 + (0.3 if (p.metric or p.top_n or p.rx(r"\bby\b|sell|spend|perform|ordered|buy|rank|selling")) else 0)
    if p.rx(r"run(?:s|ning)? out|stock[- ]?outs?|out of stock (?:soon|next|within|in)|will (?:be )?(?:go )?out of|deplet|need (?:a |to )?(?:re)?order(?:ing)? soon|running low|at risk of|shortage|likely to (?:run|go)|reorder soon|about to run|need to reorder|what.s going to run") \
            and not p.rx(r"\b(create|raise|draft|prepare|place)\b"):
        b["stockout_forecast"] += 1.6 + (0.3 if p.horizon_days else 0)
    if p.rx(r"\b(tell me about|tell me what you know about|what do you know about|rundown on|run down on|overview of|summary of|brief on|details? (?:of|for|on)|info(?:rmation)? (?:on|about)|look ?up|who is|status of|where is|profile of|how is .* performing|what is the stock (?:level )?of|what.s the stock (?:level )?of|(?:stock|inventory) levels? (?:of|for)|how much .* (?:do we have|in stock))\b") \
            and (p.ents.get("supplier") or p.ents.get("customer") or p.ents.get("product") or p.po_refs or p.so_refs or p.product_group):
        b["entity_lookup"] += 1.5 + (0.3 if (p.po_refs or p.so_refs) else 0)
    question = p.rx(r"\b(how much|how many|what.s|what is|what was|what were|total|current|tell me the|give me the|number of|count|what are|what percent\w*|rate)\b")
    if p.metric and (question or p.periods or p.rx(r"\?\s*$")) and not p.rx(r"\bwhy\b|compare|trend|\bvs\b|top|best|worst"):
        b["kpi_query"] += 1.1 if question else (0.95 if (p.metric != "orders" or p.rx(r"\?\s*$")) else 0.5)
    if (p.po_refs or p.so_refs) and not p.rx(r"\b(approve|reject|decline|deny|turn down|veto|submit|delete|remove|discard|receive|received|mark|cancel|ship|deliver|set|update|move|release|arrived|book|log|scrap|flip|push|advance|refund|put)\b"):
        b["entity_lookup"] += 1.3
    if nav_verb or p.pages_mentioned or p.statuses or p.sort_hint:
        b["show_view"] += 0.7 if not (p.metric and question) else 0.2
        if nav_verb:
            b["show_view"] += 0.4
    if p.new_tab:
        b["show_view"] += 0.8
    # conversational context: an unqualified refinement ("only delivered", "sort by newest") is a view change
    if ctx.get("page") and p.rx(r"^(only|just|sort|filter|order by|group by)\b"):
        b["show_view"] += 0.6
    return b
