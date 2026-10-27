# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    sh_pdc_status = fields.Selection(
        selection_add=[
            ('partial', 'Partially Paid'),
            ('legal', 'Escalated to Legal'),
        ],
        ondelete={'partial': 'set draft', 'legal': 'set draft'},
    )

    @api.depends('pdc_payment_ids.state', 'pdc_payment_ids.received_amount')
    def _compute_total_pdc(self):
        """Same computation as sh_pdc, but a Partially Paid PDC now
        contributes its already-received amount to 'Total Received' and
        only its remaining balance to 'Total Pending'."""
        for rec in self:
            rec.total_pdc_payment = 0.0
            rec.total_pdc_pending = 0.0
            rec.total_pdc_cancel = 0.0
            rec.total_pdc_received = 0.0
            if rec.pdc_payment_ids:
                for pdc_payment in rec.pdc_payment_ids:
                    if pdc_payment.state == 'done':
                        rec.total_pdc_received += pdc_payment.payment_amount
                    elif pdc_payment.state == 'cancel':
                        rec.total_pdc_cancel += pdc_payment.payment_amount
                    elif pdc_payment.state in ('partial', 'legal', 'bounced'):
                        rec.total_pdc_received += pdc_payment.received_amount
                        rec.total_pdc_pending += pdc_payment.balance_amount
                    else:
                        rec.total_pdc_pending += pdc_payment.payment_amount
            rec.total_pdc_payment = rec.total_pdc_pending + \
                rec.total_pdc_received + rec.total_pdc_cancel
