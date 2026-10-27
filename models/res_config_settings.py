# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pdc_max_redeposit_attempts = fields.Integer(
        string="Max PDC Redeposit Attempts",
        related='company_id.pdc_max_redeposit_attempts',
        readonly=False
    )
