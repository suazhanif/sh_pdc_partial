# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
from odoo import fields, models, _
from odoo.exceptions import UserError


class PdcReconcilePaymentWizard(models.TransientModel):
    _name = 'pdc.reconcile.payment.wizard'
    _description = 'Reconcile PDC Balance with Another Payment'

    pdc_id = fields.Many2one('pdc.wizard', string="PDC", required=True, readonly=True)
    currency_id = fields.Many2one(related='pdc_id.currency_id', readonly=True)
    balance_amount = fields.Monetary(
        related='pdc_id.balance_amount', readonly=True, string="Remaining Balance")
    journal_id = fields.Many2one(
        'account.journal', string="Payment Journal", required=True,
        domain="[('type', 'in', ['bank', 'cash'])]",
        help="Select the Cash journal if the customer paid in cash, or the "
             "Bank journal if paid by bank/wire transfer.")
    payment_date = fields.Date(string="Payment Date", default=fields.Date.today, required=True)
    reference = fields.Char(string="Reference")

    def action_confirm(self):
        self.ensure_one()
        if not self.journal_id:
            raise UserError(_("Please select a cash or bank journal for the payment."))
        self.pdc_id.action_reconcile_with_payment(
            self.journal_id, payment_date=self.payment_date, reference=self.reference)
        return {'type': 'ir.actions.client', 'tag': 'reload'}
