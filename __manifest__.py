# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
{
    "name": "Post Dated Cheque - Partial Payment",
    "author": "Baramej",
    "website": "https://baramej.io",
    "support": "baramej",
    "category": "Accounting",
    "license": "OPL-1",
    "summary": "Adds partial clearing (partially paid) support to the PDC Management module: "
                "register bank-confirmed available amounts, redeposit the cheque, "
                "and close out the remaining balance by bank transfer.",
    "description": """
Post Dated Cheque - Partial Payment
====================================

This module extends "Post Dated Cheque Management - Community Edition" (sh_pdc)
to support the real-world case where a bank only has part of the cheque amount
available when a PDC is presented for collection.

Workflow added on top of the existing Register -> Deposit flow:

After a PDC is Deposited, "Process Bank Response" asks whether the bank result
was:

1. Full Amount Deducted   -> Cheque is fully cleared (state: Done).
2. Nothing Available      -> Cheque is Bounced.
3. Partially Deducted     -> Enter the amount the bank actually deducted. The
   bank stamps and returns the cheque, the PDC moves to "Partially Paid", the
   collected amount is registered and reconciled, and the remaining balance
   stays on the cheque so it can be redeposited (configurable number of
   attempts, 3 by default).

Once the redeposit attempts are exhausted (or at any time while Partially
Paid), the PDC can be "Escalated to Legal" for collection. A Legally
Escalated PDC can still be reopened later if the customer resumes contact.

At any point while a PDC is Partially Paid or Escalated to Legal, "Reconcile
with Another Payment" can close out the remaining balance directly against a
cash or bank journal - e.g. the customer settles the rest by cash or bank
transfer instead of the cheque being redeposited again.

A full history of every bank presentation (available amount, cleared amount,
and result) is kept on the PDC record.

The original sh_pdc module and its accounting logic are not modified - this
module only inherits it and adds the new states, fields, wizards and buttons.
""",
    "version": "19.0.1.0.0",
    "depends": [
        "sh_pdc",
    ],
    "data": [
        "security/ir.model.access.csv",
        "wizard/pdc_bank_response_wizard_views.xml",
        "wizard/pdc_reconcile_payment_wizard_views.xml",
        "views/pdc_wizard_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "application": False,
    "auto_install": False,
    "installable": True,
}
