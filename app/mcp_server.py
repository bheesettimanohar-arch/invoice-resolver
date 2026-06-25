import json
import logging
from typing import Dict, Any
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("Invoice ERP Server")

# Mock database
MOCK_POS: Dict[str, Dict[str, Any]] = {
    "PO-100": {
        "po_number": "PO-100",
        "vendor": "Acme Corp",
        "items": [
            {"name": "Server Rack", "quantity": 1, "unit_price": 1200.00},
            {"name": "Switch 24-Port", "quantity": 2, "unit_price": 300.00}
        ],
        "total": 1800.00,
        "status": "OPEN"
    },
    "PO-200": {
        "po_number": "PO-200",
        "vendor": "Global Tech",
        "items": [
            {"name": "Laptop Pro", "quantity": 5, "unit_price": 1500.00}
        ],
        "total": 7500.00,
        "status": "OPEN"
    },
    "PO-300": {
        "po_number": "PO-300",
        "vendor": "Office Supplies Inc",
        "items": [
            {"name": "Ergonomic Chair", "quantity": 10, "unit_price": 250.00}
        ],
        "total": 2500.00,
        "status": "OPEN"
    }
}

MOCK_RECEIPTS: Dict[str, Dict[str, Any]] = {
    "PO-100": {
        "po_number": "PO-100",
        "items_received": [
            {"name": "Server Rack", "quantity_received": 1},
            {"name": "Switch 24-Port", "quantity_received": 2}
        ],
        "receiving_date": "2026-06-20",
        "received_by": "John Doe"
    },
    "PO-200": {
        "po_number": "PO-200",
        "items_received": [
            {"name": "Laptop Pro", "quantity_received": 4} # Mismatch: ordered 5, but received 4
        ],
        "receiving_date": "2026-06-21",
        "received_by": "Alice Smith"
    },
    "PO-300": {
        "po_number": "PO-300",
        "items_received": [
            {"name": "Ergonomic Chair", "quantity_received": 10}
        ],
        "receiving_date": "2026-06-22",
        "received_by": "Bob Johnson"
    }
}

MOCK_INVOICES: Dict[str, Dict[str, Any]] = {}

@mcp.tool()
def query_po_by_id(po_number: str) -> str:
    """Retrieve purchase order details from the ERP system.
    
    Args:
        po_number: The unique identifier of the purchase order (e.g. PO-100).
        
    Returns:
        JSON string containing the PO details (vendor, items, quantities, prices, total).
    """
    po = MOCK_POS.get(po_number.upper().strip())
    if not po:
        return json.dumps({"error": f"Purchase Order {po_number} not found."})
    return json.dumps(po)

@mcp.tool()
def query_receipt_by_po(po_number: str) -> str:
    """Retrieve receiving logs and item counts for a purchase order.
    
    Args:
        po_number: The purchase order number to look up receiving details for.
        
    Returns:
        JSON string containing the receiving logs and the quantity of items received.
    """
    receipt = MOCK_RECEIPTS.get(po_number.upper().strip())
    if not receipt:
        return json.dumps({"error": f"Receiving receipt for Purchase Order {po_number} not found."})
    return json.dumps(receipt)

@mcp.tool()
def update_invoice_status(invoice_id: str, status: str, comments: str) -> str:
    """Update the status of an invoice in the ERP database.
    
    Args:
        invoice_id: The unique identifier of the invoice.
        status: The new status of the invoice (e.g. APPROVED, NEEDS_APPROVAL, DENIED).
        comments: Audit notes or explanation for the status change.
        
    Returns:
        JSON string confirming the status update.
    """
    MOCK_INVOICES[invoice_id] = {
        "invoice_id": invoice_id,
        "status": status.upper(),
        "comments": comments
    }
    return json.dumps({
        "success": True,
        "invoice_id": invoice_id,
        "new_status": status.upper(),
        "comments": comments
    })

if __name__ == "__main__":
    # Start FastMCP stdio server
    mcp.run()
