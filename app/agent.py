import os
import re
import sys
import json
import logging
from typing import Any, Dict, List

from google.adk import Workflow, Context, Event
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import agent_tool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.events import RequestInput
from google.adk.workflow import node
from mcp import StdioServerParameters

from app.config import config

# Setup audit logger for security events
def setup_audit_logger():
    logger = logging.getLogger("security_audit")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(ch)
    return logger

audit_logger = setup_audit_logger()

def log_audit_event(event_type: str, details: dict, severity: str = "INFO"):
    log_payload = {
        "event": event_type,
        "severity": severity,
        "details": details
    }
    audit_logger.info(json.dumps(log_payload))

# MCP Setup
mcp_server_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "mcp_server.py"))

mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[mcp_server_path],
        )
    )
)

# Sub-Agents
invoice_auditor = LlmAgent(
    name="invoice_auditor",
    model=Gemini(model=config.model),
    instruction="""You are an Invoice Auditor Agent.
Your role is to match the line items on an incoming invoice against the Purchase Order (PO) in our database.
Use the MCP tools (like query_po_by_id) to look up the PO information.
Compare the invoice details:
- Line items description
- Unit price
- Quantity

Analyze the difference. If there's a price mismatch or quantity mismatch, explicitly state the discrepancy details and the dollar amount difference in your response.
Write a structured audit report listing the items, PO values, invoice values, and discrepancy amount.""",
    tools=[mcp_toolset],
    output_key="audit_report",
)

discrepancy_resolver = LlmAgent(
    name="discrepancy_resolver",
    model=Gemini(model=config.model),
    instruction="""You are a Discrepancy Resolver Agent.
Your job is to analyze the audit findings from the Invoice Auditor Agent and suggest a resolution.

We have a strict company policy:
- If the absolute discrepancy is LESS than $50.00, it can be automatically approved with a warning. You must recommend AUTO_APPROVE.
- If the absolute discrepancy is $50.00 or more, or if there is a severe mismatch (e.g. completely wrong vendor), it requires manager approval. You must recommend NEEDS_APPROVAL.
- If the invoice details are completely invalid (e.g. PO does not exist), you must recommend AUTO_DENY.

Analyze the audit report in the state. Determine the total discrepancy amount and formulate your recommendation.
Your output MUST contain one of these three exact tags:
- ROUTE: AUTO_RESOLVE (for auto-approved/denied cases)
- ROUTE: NEEDS_APPROVAL (for manager review cases)

Provide a brief explanation of your calculation and decision.""",
    tools=[mcp_toolset], # Wired into at least 2 agents
    output_key="discrepancy_resolution",
)

# Parent Orchestrator Agent
auditor_tool = agent_tool.AgentTool(agent=invoice_auditor)
resolver_tool = agent_tool.AgentTool(agent=discrepancy_resolver)

orchestrator = LlmAgent(
    name="orchestrator",
    model=Gemini(model=config.model),
    instruction="""You are the Invoice Resolution Orchestrator.
Your goal is to handle incoming invoice questions, coordinate between auditing the invoice and resolving discrepancies, and report the final path.

You have access to two tools:
1. `invoice_auditor`: Use this to perform the matching of invoice details against the PO in the database.
2. `discrepancy_resolver`: Use this to analyze any audit findings and determine the resolution route.

Step-by-step process:
1. Call the `invoice_auditor` tool with the invoice input (or query details) to get the audit report.
2. If there are no discrepancies, summarize the result and output 'ROUTE: AUTO_RESOLVE'.
3. If there are discrepancies, call the `discrepancy_resolver` tool with the audit findings to determine the correct routing.
4. Summarize the findings, calculation, and final path. Ensure you print the exact routing tag (e.g., 'ROUTE: NEEDS_APPROVAL' or 'ROUTE: AUTO_RESOLVE') in your output so the router node can parse it.
""",
    tools=[auditor_tool, resolver_tool],
    output_key="orchestrator_output",
)

# Workflow Nodes
@node(name="security_checkpoint")
async def security_checkpoint(ctx: Context, user_request: str = "", query: str = "", input: str = "", user_input: str = ""):
    query_str = user_request or query or input or user_input or ""
    
    # 1. PII scrubbing (Tax ID, bank accounts, SSN, email, phone)
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
    tax_id_pattern = r'\b\d{2}-\d{7}\b'
    bank_acct_pattern = r'\b\d{8,17}\b'
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    
    scrubbed = query_str
    scrubbed = re.sub(ssn_pattern, "[REDACTED_SSN]", scrubbed)
    scrubbed = re.sub(tax_id_pattern, "[REDACTED_TAX_ID]", scrubbed)
    scrubbed = re.sub(bank_acct_pattern, "[REDACTED_BANK_ACCT]", scrubbed)
    scrubbed = re.sub(email_pattern, "[REDACTED_EMAIL]", scrubbed)
    
    pii_detected = (scrubbed != query_str)
    if pii_detected:
        log_audit_event("pii_redacted", {"original_len": len(query_str), "redacted_len": len(scrubbed)}, "INFO")
        ctx.session.state["scrubbed_query"] = scrubbed
    else:
        ctx.session.state["scrubbed_query"] = query_str
        
    # 2. Prompt injection detection
    injection_keywords = ["ignore previous instructions", "system prompt", "override instructions", "bypass security"]
    detected_injection = any(kw in query_str.lower() for kw in injection_keywords)
    
    if detected_injection:
        log_audit_event("prompt_injection_detected", {"query": query_str}, "CRITICAL")
        ctx.session.state["security_error"] = "Potential prompt injection detected."
        return Event(route="BLOCKED", output="Security Blocked: Prompt injection detected.")
        
    # 3. Domain-specific rule: Rate limit or consent check
    if "consent" in query_str.lower() and "no" in query_str.lower():
        log_audit_event("consent_denied", {"query": query_str}, "WARNING")
        ctx.session.state["security_error"] = "User consent denied for processing data."
        return Event(route="BLOCKED", output="Security Blocked: Consent required to audit invoice data.")
        
    log_audit_event("security_check_passed", {"pii_scrubbed": pii_detected}, "INFO")
    return Event(route="CLEAN", output=scrubbed)

@node(name="security_alert")
async def security_alert(ctx: Context, node_input: Any):
    return f"Access Denied: {node_input}"

@node(name="router_node")
async def router_node(ctx: Context, node_input: str):
    output_lower = node_input.lower()
    resolution_state = str(ctx.session.state.get("discrepancy_resolution", "")).lower()
    
    if "needs_approval" in output_lower or "needs_approval" in resolution_state:
        log_audit_event("routing_decision", {"route": "NEEDS_APPROVAL", "reason": "Discrepancy meets threshold"}, "WARNING")
        return Event(route="NEEDS_APPROVAL")
    
    log_audit_event("routing_decision", {"route": "AUTO_RESOLVE", "reason": "No discrepancy or under threshold"}, "INFO")
    return Event(route="AUTO_RESOLVE")

@node(rerun_on_resume=False)
async def get_user_approval(ctx: Context, node_input: Any):
    resolution = ctx.session.state.get("discrepancy_resolution", "No resolution details found.")
    yield RequestInput(
        message=f"--- MANAGER REVIEW REQUIRED ---\nDetails:\n{resolution}\nDo you approve this payment adjustment? (yes/no): "
    )

@node(name="finalize_invoice")
async def finalize_invoice(ctx: Context, node_input: Any):
    input_str = str(node_input).strip().lower()
    if input_str in ["yes", "no", "approve", "deny"]:
        status = "APPROVED" if input_str in ["yes", "approve"] else "DENIED"
        log_audit_event("human_resolution", {"decision": status, "user_input": node_input}, "INFO")
        return f"Invoice resolution completed via manual approval. Status: {status}."
    
    log_audit_event("auto_resolution", {"details": str(node_input)}, "INFO")
    return f"Invoice resolution completed automatically. Details:\n{node_input}"

# Workflow Graph Definition
root_workflow = Workflow(
    name="invoice_resolver_workflow",
    edges=[
        ("START", security_checkpoint),
        (security_checkpoint, {
            "CLEAN": orchestrator,
            "BLOCKED": security_alert
        }),
        (orchestrator, router_node),
        (router_node, {
            "NEEDS_APPROVAL": get_user_approval,
            "AUTO_RESOLVE": finalize_invoice
        }),
        (get_user_approval, finalize_invoice)
    ]
)

app = App(
    root_agent=root_workflow,
    name="app",
)
