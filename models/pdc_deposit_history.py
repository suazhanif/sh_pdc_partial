# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
from odoo import fields, models


class PdcDepositHistory(models.Model):
    _name = 'pdc.deposit.history'
    _description = 'PDC Bank Presentation History'
    _order = 'pdc_id, attempt_number'

    pdc_id = fields.Many2one(
        'pdc.wizard', string="PDC", required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(
        'res.currency', string="Currency", related='pdc_id.currency_id')
    company_id = fields.Many2one(
        'res.company', string='Company', related='pdc_id.company_id', store=True)
    attempt_number = fields.Integer(string="Attempt #", required=True)
    date = fields.Date(string="Date", required=True, default=fields.Date.today)
    available_amount = fields.Monetary(
        string="Amount Available in Bank",
        help="What the bank reported as available when the cheque was presented.")
    cleared_amount = fields.Monetary(
        string="Amount Cleared",
        help="Amount actually collected/reconciled in this round.")
    result = fields.Selection([
        ('done', 'Fully Cleared'),
        ('partial', 'Partially Cleared'),
        ('bounced', 'Bounced'),
        ('redeposited', 'Redeposited'),
        ('reconciled', 'Reconciled with Other Payment'),
        ('legal', 'Escalated to Legal'),
        ('reopened', 'Reopened from Legal'),
    ], string="Result", required=True)
    note = fields.Char(string="Note")
