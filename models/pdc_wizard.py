# -*- coding: utf-8 -*-
# Copyright (C) Baramej.
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PDCWizard(models.Model):
    """Inherits the PDC Wizard to support partial bank collection of a
    Post Dated Cheque.

    Flow on top of the existing Register -> Deposit flow::

        Deposited --(bank confirms full amount)-----------> Done
        Deposited --(bank confirms nothing)----------------> Bounced
        Deposited --(bank confirms part of it)--------------> Partially Paid

        Partially Paid --(redeposit, bank confirms rest)-----> Done
        Partially Paid --(redeposit, still partial, < max)----> Partially Paid
        Partially Paid --(redeposit attempts exhausted)-------> (manual) Escalate to Legal
        Partially Paid --(customer settles by other payment)--> Done

        Escalated to Legal --(customer eventually settles)----> Done
        Escalated to Legal --(reopen)--------------------------> Partially Paid

    None of the accounting logic that already exists in sh_pdc is modified;
    this model only adds the new states, the fields needed to track partial
    collections, and the methods that drive the extra journal entries.
    """
    _inherit = 'pdc.wizard'

    state = fields.Selection(
        selection_add=[
            ('partial', 'Partially Paid'),
            ('legal', 'Escalated to Legal'),
        ],
        ondelete={'partial': 'set default', 'legal': 'set default'},
    )

    received_amount = fields.Monetary(
        string="Received Amount", default=0.0, readonly=True, tracking=True,
        help="Cumulative amount actually collected/cleared to the bank across "
             "all presentations of this cheque.")
    balance_amount = fields.Monetary(
        string="Remaining Balance", compute='_compute_balance_amount', store=True,
        help="Payment Amount minus what has already been received.")
    redeposit_count = fields.Integer(
        string="Redeposit Count", default=0, readonly=True, tracking=True,
        help="Number of times this cheque has been partially cleared and "
             "sent back to the bank for collection.")
    max_redeposit_attempts = fields.Integer(
        string="Max Redeposit Attempts", related='company_id.pdc_max_redeposit_attempts',
        readonly=True,
        help="Configured in Settings > Accounting > PDC Payment.")
    can_redeposit = fields.Boolean(
        string="Can Redeposit", compute='_compute_can_redeposit',
        help="Whether another bank presentation is still allowed for this cheque.")
    legal_escalation_date = fields.Date(string="Escalated On", readonly=True, tracking=True)
    deposit_history_ids = fields.One2many(
        'pdc.deposit.history', 'pdc_id', string="Bank Presentation History")

    @api.depends('payment_amount', 'received_amount')
    def _compute_balance_amount(self):
        for rec in self:
            rec.balance_amount = rec.payment_amount - rec.received_amount

    @api.depends('state', 'redeposit_count', 'max_redeposit_attempts')
    def _compute_can_redeposit(self):
        for rec in self:
            if rec.state == 'deposited':
                # The current, not-yet-resolved presentation is always processable.
                rec.can_redeposit = True
            elif rec.state in ('partial', 'bounced'):
                rec.can_redeposit = rec.redeposit_count < (rec.max_redeposit_attempts or 3)
            else:
                rec.can_redeposit = False

    @api.constrains('payment_amount', 'received_amount', 'invoice_id', 'invoice_ids', 'state')
    def _check_pdc_amount_limit(self):
        """Same guard as sh_pdc (a PDC's exposure against an invoice can't
        exceed what's still owed on it), but adapted for partial clearance:
        once a PDC has genuinely collected part of its amount, its
        remaining exposure against the invoice is its balance_amount (what's
        still expected), not its original face payment_amount - the invoice's
        own amount_residual already shrinks as each partial amount is
        recognised, so comparing against the untouched face value would
        false-positive."""
        if self.env.context.get('skip_pdc_limit'):
            return
        for rec in self:
            invoices = rec.invoice_ids | rec.invoice_id
            if invoices and rec.state not in ('cancel', 'bounced', 'done'):
                invoice_ids = invoices.ids
                domain = [
                    '|',
                    ('invoice_id', 'in', invoice_ids),
                    ('invoice_ids', 'in', invoice_ids),
                    ('state', 'in', ['draft', 'registered', 'returned', 'deposited', 'partial', 'legal']),
                    ('id', '!=', rec.id)
                ]
                ignore_id = self.env.context.get('ignore_pdc_id')
                if ignore_id:
                    domain.append(('id', '!=', ignore_id))

                existing_pdcs = self.env['pdc.wizard'].search(domain)
                total_existing = sum(
                    p.balance_amount if p.state in ('partial', 'legal') else p.payment_amount
                    for p in existing_pdcs
                )
                total_invoice_residual = sum(invoices.mapped('amount_residual'))

                rec_amount = rec.balance_amount if rec.state in ('partial', 'legal') else rec.payment_amount
                total_requested = round(total_existing + rec_amount, 2)
                residual_rounded = round(total_invoice_residual, 2)

                if total_requested > residual_rounded and residual_rounded != 0:
                    raise ValidationError(self.env._(
                        "The total PDC amount (%(requested)s) exceeds the remaining invoice amount (%(residual)s)!"
                    ) % {
                        'requested': total_requested,
                        'residual': residual_rounded
                    })

    # ------------------------------------------------------------
    # Wizard openers (called from the form view buttons)
    # ------------------------------------------------------------
    def action_open_bank_response_wizard(self):
        """Opens the wizard used to register what the bank confirmed as
        available when the cheque was presented for collection."""
        self.ensure_one()
        if self.state != 'deposited':
            raise UserError(_(
                "Only a Deposited PDC can be processed for a bank response. "
                "If this cheque is Partially Paid or Bounced, redeposit it first."))
        return {
            'name': _('Process Bank Response'),
            'type': 'ir.actions.act_window',
            'res_model': 'pdc.bank.response.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_pdc_id': self.id,
                'default_deducted_amount': self.balance_amount,
            },
        }

    def action_redeposit_partial(self):
        """Re-presents a Partially Paid cheque to the bank. The remaining
        balance already sits unreconciled in the PDC suspense account from
        the original deposit, so this does not create a new journal entry -
        it simply puts the cheque back into 'Deposited' (awaiting the next
        bank result) and consumes one of the limited redeposit attempts,
        exactly like redepositing a Bounced cheque does."""
        self.ensure_one()
        if self.state != 'partial':
            raise UserError(_("Only a Partially Paid PDC can be redeposited."))
        if not self.can_redeposit:
            raise UserError(_(
                "This cheque has reached the maximum number of redeposit attempts (%s). "
                "Please Escalate to Legal or Reconcile with another Payment."
            ) % (self.max_redeposit_attempts or 3))
        self.redeposit_count += 1
        self.write({'state': 'deposited'})
        self._log_deposit_history(
            0.0, 0.0, 'redeposited',
            _("Cheque redeposited after partial clearance (attempt %s of %s).")
            % (self.redeposit_count, self.max_redeposit_attempts or 3))

    def action_open_reconcile_payment_wizard(self):
        """Opens the wizard used to close out the remaining balance because
        the customer settled it directly by cash or bank transfer."""
        self.ensure_one()
        if self.state not in ('partial', 'legal', 'bounced'):
            raise UserError(_(
                "Only a Partially Paid, Bounced or Legally Escalated PDC can be "
                "reconciled with another payment."))
        if self.currency_id.is_zero(self.balance_amount):
            raise UserError(_("There is no remaining balance to settle."))
        return {
            'name': _('Reconcile with Another Payment'),
            'type': 'ir.actions.act_window',
            'res_model': 'pdc.reconcile.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_pdc_id': self.id,
            },
        }

    # ------------------------------------------------------------
    # Accounting helpers for arbitrary (partial) amounts
    # ------------------------------------------------------------
    def _get_clearing_bank_account(self, journal):
        """Returns the outstanding account id to use on the bank side of a
        clearing entry for the given journal."""
        if self.payment_type == 'receive_money':
            accounts = journal._get_journal_inbound_outstanding_payment_accounts()
        else:
            accounts = journal._get_journal_outbound_outstanding_payment_accounts()
        account = accounts[0].id if accounts else False
        if not account:
            raise UserError(_(
                "The journal '%s' is missing an outstanding payment account.") % journal.display_name)
        return account

    def _prepare_clearing_line_vals(self, account_id, amount, is_debit, name, line_date):
        vals = {
            'pdc_id': self.id,
            'partner_id': self.partner_id.commercial_partner_id.id,
            'account_id': account_id,
            'ref': self.memo,
            'name': name,
            'date': line_date,
            'date_maturity': self.due_date,
            'company_id': self.company_id.id,
        }
        if is_debit:
            vals['debit'] = amount
        else:
            vals['credit'] = amount
        return vals

    def _create_clearing_move(self, journal, amount, name, move_date=False, target_account=False):
        """Creates and posts a journal entry moving `amount` between
        `target_account` (defaults to the PDC suspense account) and the
        bank/cash outstanding account of `journal`. Returns (move, target_account_id)."""
        self.ensure_one()
        move_date = move_date or fields.Date.today()
        clearing_account = target_account or self.check_pdc_account()
        bank_account = self._get_clearing_bank_account(journal)

        if self.payment_type == 'receive_money':
            debit_vals = self._prepare_clearing_line_vals(bank_account, amount, True, name, move_date)
            credit_vals = self._prepare_clearing_line_vals(clearing_account, amount, False, name, move_date)
        else:
            debit_vals = self._prepare_clearing_line_vals(clearing_account, amount, True, name, move_date)
            credit_vals = self._prepare_clearing_line_vals(bank_account, amount, False, name, move_date)

        move_vals = {
            'pdc_id': self.id,
            'date': move_date,
            'journal_id': journal.id,
            'partner_id': self.partner_id.commercial_partner_id.id,
            'ref': self.memo,
            'move_type': 'entry',
            'company_id': self.company_id.id,
            'line_ids': [(0, 0, debit_vals), (0, 0, credit_vals)],
        }
        move = self.env['account.move'].create(move_vals)
        move.action_post()
        return move, clearing_account

    def _reconcile_pdc_suspense(self, move, pdc_account):
        """Reconciles the PDC suspense side of a new clearing move against
        the original deposit entry. Odoo handles the amount matching itself,
        so a cheque that is only partially cleared simply leaves the
        remaining balance open on the original deposit line for next time."""
        lines = (self.deposited_debit | self.deposited_credit | move.line_ids).filtered(
            lambda l: l.account_id.id == pdc_account and not l.reconciled
        )
        if len(lines) > 1:
            lines.reconcile()

    def _finalize_invoice_reconciliation(self):
        """Reconciles the invoice(s) against the original deposit entry on
        the partner (receivable/payable) side. Safe to call whether this is
        the very first settlement or the last of several partial ones: by
        that point both sides' remaining residual are already in lockstep
        (kept that way by _partial_settle_invoice below), so a direct match
        closes exactly what's left."""
        domain = self.get_reconcilation_domain()
        pdc_partner_lines = self.deposited_debit | self.deposited_credit
        (self.invoice_ids.line_ids | pdc_partner_lines).filtered_domain(domain).reconcile()

    def _partial_settle_invoice(self, clear_amount, journal, move_date=False):
        """Settles exactly `clear_amount` of the invoice, without touching
        the still-pending remainder of the original deposit pledge.

        The original deposit entry pledged the *full* cheque amount against
        the invoice in one go, to be reconciled only once the cheque fully
        clears. To recognise partial bank clearances on the invoice as they
        happen, this posts a small balanced "wash" entry on the partner's
        receivable/payable account:
          - one leg re-consumes `clear_amount` of the still-open deposit
            pledge (so the pledge's open balance shrinks in step with what
            has genuinely been collected so far);
          - the other leg partially reconciles `clear_amount` against the
            invoice's own receivable/payable line.
        Net effect on the partner account is zero - this entry exists purely
        to redirect reconciliation, not to move real value - while Odoo's
        own partial-reconciliation handles the invoice's amount_residual and
        payment_state (e.g. flips to 'partial') correctly.
        """
        self.ensure_one()
        move_date = move_date or fields.Date.today()
        partner_account = self.get_partner_account()
        name = _("PDC Partial Invoice Settlement")
        is_receive = self.payment_type == 'receive_money'

        pledge_leg_vals = self._prepare_clearing_line_vals(partner_account, clear_amount, is_receive, name, move_date)
        invoice_leg_vals = self._prepare_clearing_line_vals(partner_account, clear_amount, not is_receive, name, move_date)

        move = self.env['account.move'].create({
            'pdc_id': self.id,
            'date': move_date,
            'journal_id': journal.id,
            'partner_id': self.partner_id.commercial_partner_id.id,
            'ref': self.memo,
            'move_type': 'entry',
            'company_id': self.company_id.id,
            'line_ids': [(0, 0, pledge_leg_vals), (0, 0, invoice_leg_vals)],
        })
        move.action_post()

        pledge_leg = move.line_ids.filtered(lambda l: l.debit > 0) if is_receive else move.line_ids.filtered(lambda l: l.credit > 0)
        invoice_leg = move.line_ids - pledge_leg

        domain = self.get_reconcilation_domain()
        pdc_partner_lines = (self.deposited_debit | self.deposited_credit).filtered_domain(domain)
        if pdc_partner_lines:
            (pdc_partner_lines | pledge_leg).reconcile()

        invoice_lines = self.invoice_ids.line_ids.filtered_domain(domain)
        if invoice_lines:
            (invoice_lines | invoice_leg).reconcile()

    def _log_deposit_history(self, available_amount, cleared_amount, result, note=False):
        self.ensure_one()
        self.env['pdc.deposit.history'].create({
            'pdc_id': self.id,
            'attempt_number': len(self.deposit_history_ids) + 1,
            'date': fields.Date.today(),
            'available_amount': available_amount,
            'cleared_amount': cleared_amount,
            'result': result,
            'note': note,
        })

    # ------------------------------------------------------------
    # Main entry point: register what the bank confirmed
    # ------------------------------------------------------------
    def action_process_bank_response(self, available_amount, note=False):
        """Registers the bank's response for this presentation of the
        cheque and drives the PDC to the right outcome:
            - available_amount == 0                -> Bounced
            - available_amount >= remaining balance -> Done (fully cleared)
            - 0 < available_amount < remaining       -> Partially Paid
        """
        self.ensure_one()
        if self.state != 'deposited':
            raise UserError(_("Only a Deposited PDC can be processed for a bank response."))
        if available_amount < 0:
            raise UserError(_("The available amount cannot be negative."))

        remaining = self.currency_id.round(self.balance_amount)

        # Option 1: nothing available -> Bounced
        if self.currency_id.is_zero(available_amount):
            self.action_bounced()
            return

        clear_amount = min(self.currency_id.round(available_amount), remaining)
        will_fully_clear = clear_amount >= remaining or self.currency_id.is_zero(remaining - clear_amount)

        move, pdc_account = self._create_clearing_move(
            self.journal_id, clear_amount, _("PDC Bank Collection"))
        self._reconcile_pdc_suspense(move, pdc_account)

        self.received_amount = self.received_amount + clear_amount

        if will_fully_clear:
            # Option 2: fully covers the remaining balance -> Done
            self._finalize_invoice_reconciliation()
            self.write({'state': 'done', 'done_date': fields.Date.today()})
            self._log_deposit_history(
                available_amount, clear_amount, 'done',
                note or _("Cheque fully cleared."))
        else:
            # Option 3: bank deducted the available amount, stamped and
            # returned the cheque for the remaining balance -> Partially Paid.
            # Note: the redeposit counter itself is only incremented when the
            # cheque is actually redeposited (action_redeposit_partial /
            # action_deposited), not here.
            self._partial_settle_invoice(clear_amount, self.journal_id, fields.Date.today())
            self.write({'state': 'partial'})
            self._log_deposit_history(
                available_amount, clear_amount, 'partial',
                note or _(
                    "Bank had only %(available)s available; it deducted that amount, "
                    "stamped and returned the cheque for the remaining balance of %(balance)s."
                ) % {'available': clear_amount, 'balance': self.currency_id.round(remaining - clear_amount)})

    def action_reconcile_with_payment(self, journal, payment_date=False, reference=False):
        """Closes out the remaining balance of a Partially Paid / Bounced /
        Legally Escalated PDC because the customer paid the rest directly by
        cash or bank transfer, instead of the cheque being redeposited again.

        This is done in two steps:
          1. If the PDC still has an open deposit pledge (came from a partial
             collection), that pledge is written off - the cheque itself will
             never deliver this remaining amount, since it's being settled
             another way. This uses the same write-off mechanics as a full
             Bounce, just scoped to the remaining balance.
          2. A real, registered account.payment is created for the remaining
             amount and reconciled directly against the invoice, so it shows
             up under Accounting > Payments like any other payment.
        """
        self.ensure_one()
        if self.state not in ('partial', 'legal', 'bounced'):
            raise UserError(_(
                "Only a Partially Paid, Bounced or Legally Escalated PDC can be "
                "reconciled with another payment."))
        if not journal:
            raise UserError(_("Please select a cash or bank journal for the payment."))

        remaining = self.currency_id.round(self.balance_amount)
        if remaining <= 0 or self.currency_id.is_zero(remaining):
            raise UserError(_("There is no remaining balance to settle."))

        move_date = payment_date or fields.Date.today()
        domain = self.get_reconcilation_domain()
        partner_account = self.get_partner_account()

        # Step 1: write off whatever is left of the original deposit pledge.
        pdc_partner_lines = (self.deposited_debit | self.deposited_credit).filtered_domain(domain)
        if pdc_partner_lines:
            pdc_account = self.check_pdc_account()
            writeoff_name = _("PDC Pledge Written Off (Settled by Other Payment)")
            is_receive = self.payment_type == 'receive_money'
            debit_vals = self._prepare_clearing_line_vals(
                partner_account if is_receive else pdc_account, remaining, True, writeoff_name, move_date)
            credit_vals = self._prepare_clearing_line_vals(
                pdc_account if is_receive else partner_account, remaining, False, writeoff_name, move_date)
            writeoff_move = self.env['account.move'].create({
                'pdc_id': self.id,
                'date': move_date,
                'journal_id': self.journal_id.id,
                'partner_id': self.partner_id.commercial_partner_id.id,
                'ref': self.memo,
                'move_type': 'entry',
                'company_id': self.company_id.id,
                'line_ids': [(0, 0, debit_vals), (0, 0, credit_vals)],
            })
            writeoff_move.action_post()
            original_lines = self.deposited_debit | self.deposited_credit
            for account_id in (partner_account, pdc_account):
                lines = (original_lines | writeoff_move.line_ids).filtered(
                    lambda r: r.account_id.id == account_id and not r.reconciled)
                if len(lines) > 1:
                    lines.reconcile()

        # Step 2: register a real payment for the remaining balance and
        # reconcile it directly against the invoice.
        memo = _("PDC Balance Reconciled with Other Payment")
        if reference:
            memo = "%s - %s" % (memo, reference)

        payment = self.env['account.payment'].create({
            'payment_type': 'inbound' if self.payment_type == 'receive_money' else 'outbound',
            'partner_type': 'customer' if self.payment_type == 'receive_money' else 'supplier',
            'partner_id': self.partner_id.commercial_partner_id.id,
            'amount': remaining,
            'journal_id': journal.id,
            'date': move_date,
            'memo': memo,
            'company_id': self.company_id.id,
            'pdc_id': self.id,
        })
        payment.action_post()

        payment_partner_line = payment.move_id.line_ids.filtered(
            lambda l: l.account_id.id == partner_account and not l.reconciled)
        if not payment_partner_line:
            raise UserError(_(
                "Could not find a receivable/payable line on the registered payment "
                "to reconcile against the invoice; please check the journal's "
                "payment accounting configuration."))

        invoice_lines = self.invoice_ids.line_ids.filtered_domain(domain)
        if invoice_lines:
            (invoice_lines | payment_partner_line).reconcile()

        self.received_amount = self.payment_amount
        self.write({'state': 'done', 'done_date': fields.Date.today()})
        self._log_deposit_history(
            remaining, remaining, 'reconciled',
            _("Remaining balance of %(balance)s settled via a registered payment on %(journal)s (%(type)s).")
            % {'balance': remaining, 'journal': journal.display_name, 'type': journal.type})

    def action_escalate_to_legal(self):
        """Manually escalates a Partially Paid or Bounced PDC to legal
        collection, typically once redeposit attempts are exhausted."""
        self.ensure_one()
        if self.state not in ('partial', 'bounced'):
            raise UserError(_("Only a Partially Paid or Bounced PDC can be escalated to Legal."))
        self.write({
            'state': 'legal',
            'legal_escalation_date': fields.Date.today(),
        })
        self._log_deposit_history(
            0.0, 0.0, 'legal',
            _("Escalated to Legal with a remaining balance of %s.") % self.currency_id.round(self.balance_amount))

    def action_reopen_from_legal(self):
        """Reopens a Legally Escalated PDC back to Partially Paid, e.g. if
        the customer resumes contact/negotiation. Available balance may then
        be redeposited again or reconciled with another payment."""
        self.ensure_one()
        if self.state != 'legal':
            raise UserError(_("Only a Legally Escalated PDC can be reopened."))
        self.write({'state': 'partial'})
        self._log_deposit_history(0.0, 0.0, 'reopened', _("Reopened from Legal Escalation."))

    # ------------------------------------------------------------
    # Keep the history/counters in sync when the original quick actions
    # (Deposit / Bounce / Done) are used
    # ------------------------------------------------------------
    def action_deposited(self):
        """Wraps the base 'Deposit' action so that redepositing a Bounced
        cheque also consumes one of the shared redeposit attempts, exactly
        like redepositing a Partially Paid cheque does."""
        is_redeposit_ids = self.filtered(lambda r: r.state == 'bounced').ids
        res = super().action_deposited()
        for rec in self.browse(is_redeposit_ids):
            rec.redeposit_count += 1
            rec._log_deposit_history(
                0.0, 0.0, 'redeposited',
                _("Cheque redeposited after bouncing (attempt %s of %s).")
                % (rec.redeposit_count, rec.max_redeposit_attempts or 3))
        return res

    def action_bounced(self):
        """Wraps the base 'Bounce' action so that a cheque which already had
        part of its amount genuinely collected in an earlier round only has
        its *remaining* balance reversed, instead of the base method's fixed
        full cheque amount (which would incorrectly wipe out the amount
        already collected)."""
        no_prior_collection = self.filtered(lambda r: not r.received_amount)
        with_prior_collection = self - no_prior_collection

        res = True
        if no_prior_collection:
            res = super(PDCWizard, no_prior_collection).action_bounced()
        for rec in with_prior_collection:
            rec._bounce_remaining_balance()

        for rec in self:
            already_logged = rec.deposit_history_ids.filtered(
                lambda h: h.date == fields.Date.today() and h.result == 'bounced')
            if not already_logged:
                rec._log_deposit_history(0.0, 0.0, 'bounced', _("Cheque bounced."))
        return res

    def _bounce_remaining_balance(self):
        """Reverses only the remaining unreconciled balance of a cheque that
        already had some amount genuinely collected in an earlier round."""
        self.ensure_one()
        remaining = self.currency_id.round(self.balance_amount)
        if remaining <= 0 or self.currency_id.is_zero(remaining):
            self.write({'state': 'bounced', 'deposited_debit': False, 'deposited_credit': False})
            return

        pdc_account = self.check_pdc_account()
        partner_account = self.get_partner_account()
        name = _("PDC Payment Bounce (remaining balance)")
        move_date = fields.Date.today()

        if self.payment_type == 'receive_money':
            debit_vals = self._prepare_clearing_line_vals(partner_account, remaining, True, name, move_date)
            credit_vals = self._prepare_clearing_line_vals(pdc_account, remaining, False, name, move_date)
        else:
            debit_vals = self._prepare_clearing_line_vals(pdc_account, remaining, True, name, move_date)
            credit_vals = self._prepare_clearing_line_vals(partner_account, remaining, False, name, move_date)

        move_vals = {
            'pdc_id': self.id,
            'date': move_date,
            'journal_id': self.journal_id.id,
            'partner_id': self.partner_id.commercial_partner_id.id,
            'ref': self.memo,
            'move_type': 'entry',
            'company_id': self.company_id.id,
            'line_ids': [(0, 0, debit_vals), (0, 0, credit_vals)],
        }
        bounce_move = self.env['account.move'].create(move_vals)
        bounce_move.action_post()

        original_deposit_lines = self.deposited_debit | self.deposited_credit
        for account_id in (partner_account, pdc_account):
            lines = (original_deposit_lines | bounce_move.line_ids).filtered(
                lambda r: r.account_id.id == account_id and not r.reconciled)
            if len(lines) > 1:
                lines.reconcile()

        self.write({
            'state': 'bounced',
            'deposited_debit': False,
            'deposited_credit': False,
        })

    def action_done(self):
        res = super().action_done()
        for rec in self:
            if rec.received_amount < rec.payment_amount:
                rec.received_amount = rec.payment_amount
            already_logged = rec.deposit_history_ids.filtered(
                lambda h: h.date == fields.Date.today() and h.result == 'done')
            if not already_logged:
                rec._log_deposit_history(rec.payment_amount, rec.payment_amount, 'done', _("Cheque fully cleared."))
        return res
