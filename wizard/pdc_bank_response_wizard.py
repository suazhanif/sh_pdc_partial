# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PdcBankResponseWizard(models.TransientModel):
    _name = 'pdc.bank.response.wizard'
    _description = 'Register PDC Bank Response'

    pdc_id = fields.Many2one('pdc.wizard', string="PDC", required=True, readonly=True)
    currency_id = fields.Many2one(related='pdc_id.currency_id', readonly=True)
    payment_amount = fields.Monetary(
        related='pdc_id.payment_amount', readonly=True, string="Cheque Amount")
    received_amount = fields.Monetary(
        related='pdc_id.received_amount', readonly=True, string="Already Received")
    balance_amount = fields.Monetary(
        related='pdc_id.balance_amount', readonly=True, string="Balance Due")
    redeposit_count = fields.Integer(
        related='pdc_id.redeposit_count', readonly=True, string="Redeposits So Far")
    max_redeposit_attempts = fields.Integer(
        related='pdc_id.max_redeposit_attempts', readonly=True)

    outcome = fields.Selection([
        ('full', 'Full Amount Deducted (cheque fully cleared)'),
        ('partial', 'Partially Deducted (bank stamped and returned the cheque)'),
        ('none', 'Nothing Available (cheque bounced)'),
    ], string="Bank Result", required=True, default='full')
    deducted_amount = fields.Monetary(
        string="Deducted Amount",
        help="Amount the bank actually deducted/collected in this round. "
             "Required when the result is 'Partially Deducted'.")
    note = fields.Char(string="Note")

    @api.onchange('outcome', 'balance_amount')
    def _onchange_outcome(self):
        if self.outcome == 'full':
            self.deducted_amount = self.balance_amount
        elif self.outcome == 'none':
            self.deducted_amount = 0.0

    def action_confirm(self):
        self.ensure_one()
        if self.outcome == 'full':
            available_amount = self.balance_amount
        elif self.outcome == 'none':
            available_amount = 0.0
        else:
            if self.deducted_amount <= 0:
                raise UserError(_("Please enter the amount the bank actually deducted."))
            if self.currency_id.compare_amounts(self.deducted_amount, self.balance_amount) >= 0:
                raise UserError(_(
                    "The deducted amount must be less than the balance due (%s). "
                    "Use 'Full Amount Deducted' if the cheque cleared completely."
                ) % self.balance_amount)
            available_amount = self.deducted_amount

        self.pdc_id.action_process_bank_response(available_amount, note=self.note)
        # Force the underlying PDC form to refetch so the new state/amounts
        # are visible immediately instead of appearing "stuck".
        return {'type': 'ir.actions.client', 'tag': 'reload'}
