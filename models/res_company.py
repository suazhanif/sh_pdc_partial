# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    pdc_max_redeposit_attempts = fields.Integer(
        string="Max PDC Redeposit Attempts",
        default=3,
        help="Maximum number of times a Post Dated Cheque may be redeposited "
             "after a partial bank collection before the remaining balance "
             "must be settled by bank transfer."
    )
