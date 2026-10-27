# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    pdc_id = fields.Many2one(
        'pdc.wizard', string="PDC", readonly=True, copy=False,
        help="Set when this payment was registered to settle the remaining "
             "balance of a Partially Paid / Bounced / Legally Escalated PDC, "
             "via 'Reconcile with Another Payment'.")
