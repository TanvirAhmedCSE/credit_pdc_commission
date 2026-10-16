from odoo import fields, models, tools

from .utils import BUCKETS


class CcAgingReport(models.Model):
    _name = 'cc.aging.report'
    _description = 'Receivable Aging'
    _auto = False
    _order = 'days_overdue desc'

    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    partner_id = fields.Many2one('res.partner', "Customer", readonly=True)
    user_id = fields.Many2one('res.users', "Salesperson", readonly=True)
    move_id = fields.Many2one('account.move', "Journal Entry", readonly=True)
    due_date = fields.Date(readonly=True)
    days_overdue = fields.Integer("Days Overdue", readonly=True, aggregator='max')
    bucket = fields.Selection(BUCKETS, "Aging Bucket", readonly=True)
    residual = fields.Monetary("Amount Due", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    aml.id AS id,
                    aml.company_id AS company_id,
                    aml.partner_id AS partner_id,
                    aml.move_id AS move_id,
                    am.invoice_user_id AS user_id,
                    COALESCE(aml.date_maturity, aml.date) AS due_date,
                    (CURRENT_DATE - COALESCE(aml.date_maturity, aml.date)) AS days_overdue,
                    CASE
                        WHEN (CURRENT_DATE - COALESCE(aml.date_maturity, aml.date)) <= 0 THEN 'not_due'
                        WHEN (CURRENT_DATE - COALESCE(aml.date_maturity, aml.date)) <= 30 THEN 'd1_30'
                        WHEN (CURRENT_DATE - COALESCE(aml.date_maturity, aml.date)) <= 60 THEN 'd31_60'
                        WHEN (CURRENT_DATE - COALESCE(aml.date_maturity, aml.date)) <= 90 THEN 'd61_90'
                        ELSE 'd90_plus'
                    END AS bucket,
                    aml.amount_residual AS residual
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                JOIN account_account aa ON aa.id = aml.account_id
                WHERE aml.parent_state = 'posted'
                  AND aa.account_type = 'asset_receivable'
                  AND aml.reconciled IS NOT TRUE
                  AND aml.amount_residual <> 0
            )
        """ % self._table)
