# -*- coding: utf-8 -*-

import os
import time
import logging
import itertools

from jinja2 import Environment, FileSystemLoader

from openerp import fields, models, api, _
from openerp.exceptions import Warning as UserError
from openerp.exceptions import ValidationError
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT, DEFAULT_SERVER_DATETIME_FORMAT

from . import utils
from ..xades.sri import DocumentXML
from ..xades.xades import Xades


class AccountWithdrawing(models.Model):

    _name = 'account.retention'
    _inherit = ['account.retention', 'account.edocument']
    _logger = logging.getLogger(_name)


    reference = fields.Char(
        "Reference",
        compute='_get_secuencial')

    @api.depends('name')
    def _get_secuencial(self):
        self.reference = self.name and self.name[-9:] or "000000000"

    def get_secuencial(self):
        return self.name and self.name[-9:] or "000000000"
        # return getattr(self, 'name')[-9:]

    def _info_withdrawing(self, withdrawing):
        """
        """
        # generar infoTributaria
        company = withdrawing.company_id
        partner = withdrawing.invoice_id.partner_id
        if not partner.vat_type:
            raise ValidationError(_("Partner must have a VAT type."))
        if not partner.identifier:
            raise ValidationError(_("Partner must have an identifier."))
        if not withdrawing.tax_ids:
            raise ValidationError(_("Withdrawing must have taxes."))
        infoCompRetencion = {
            'fechaEmision': time.strftime('%d/%m/%Y', time.strptime(withdrawing.date, '%Y-%m-%d')),  # noqa
            'dirEstablecimiento': company.street,
            'obligadoContabilidad': 'SI',
            'tipoIdentificacionSujetoRetenido': utils.tipoIdentificacion[partner.vat_type],  # noqa
            'razonSocialSujetoRetenido': partner.name,
            'identificacionSujetoRetenido': partner.identifier,
            'periodoFiscal': time.strftime('%m/%Y', time.strptime(withdrawing.date, DEFAULT_SERVER_DATE_FORMAT)),
            }
        if company.company_registry:
            infoCompRetencion.update({'contribuyenteEspecial': company.company_registry})  # noqa
        return infoCompRetencion

    def _impuestos(self, retention):
        """
        """
        def get_codigo_retencion(linea):
            if linea.group_id.code in ['ret_vat_b', 'ret_vat_srv']:
                return utils.tabla21[line.tax_id.percent_report]
            else:
                # code = linea.base_code_id and linea.base_code_id.code or linea.tax_code_id.code  # noqa
                return linea.group_id.code

        impuestos = []
        for line in retention.tax_ids:
            impuesto = {
                'codigo': utils.tabla20[line.group_id.code],
                'codigoRetencion': get_codigo_retencion(line),
                'baseImponible': '%.2f' % (line.base),
                'porcentajeRetener': str(line.tax_id.percent_report),
                'valorRetenido': '%.2f' % (abs(line.amount)),
                'codDocSustento': retention.invoice_id.sustento_id.code,
                'numDocSustento': retention.invoice_id.invoice_number[:15],
                'fechaEmisionDocSustento': time.strftime('%d/%m/%Y', time.strptime(retention.invoice_id.date_invoice, DEFAULT_SERVER_DATE_FORMAT))  # noqa
            }
            impuestos.append(impuesto)
        return {'impuestos': impuestos}

    def render_document(self, document, access_key, issuing_code):
        tmpl_path = os.path.join(os.path.dirname(__file__), 'templates')
        env = Environment(loader=FileSystemLoader(tmpl_path))
        ewithdrawing_tmpl = env.get_template('ewithdrawing.xml')
        data = {}
        data.update(self._info_tributaria(document, access_key, issuing_code))
        data.update(self._info_withdrawing(document))
        data.update(self._impuestos(document))
        edocument = ewithdrawing_tmpl.render(data)
        self._logger.debug(edocument)
        return edocument

    def render_authorized_document(self, autorizacion):
        tmpl_path = os.path.join(os.path.dirname(__file__), 'templates')
        env = Environment(loader=FileSystemLoader(tmpl_path))
        edocument_tmpl = env.get_template('authorized_withdrawing.xml')
        auth_xml = {
            'estado': autorizacion.estado,
            'numeroAutorizacion': autorizacion.numeroAutorizacion,
            'ambiente': autorizacion.ambiente,
            'fechaAutorizacion': str(autorizacion.fechaAutorizacion.strftime("%d/%m/%Y %H:%M:%S")),  # noqa
            'comprobante': autorizacion.comprobante
        }
        auth_withdrawing = edocument_tmpl.render(auth_xml)
        return auth_withdrawing

    @api.multi
    def action_generate_document(self):
        """
        Generate SRI electronic voucher
        """
        for obj in self:
            self.check_date(obj.date)
            self.check_before_sent()
            access_key, issuing_code = self._get_codes('account.retention')
            ewithdrawing = self.render_document(obj, access_key, issuing_code)
            self._logger.debug(ewithdrawing)
            inv_xml = DocumentXML(ewithdrawing, 'withdrawing')
            inv_xml.validate_xml()
            xades = Xades()
            file_pk12 = obj.company_id.electronic_signature
            password = obj.company_id.password_electronic_signature
            signed_document = xades.sign(ewithdrawing, file_pk12, password)
            ok, errores = inv_xml.send_receipt(signed_document)
            if not ok:
                raise UserError(errores)
            auth, m = inv_xml.request_authorization(access_key)
            if not auth:
                msg = ' '.join(list(itertools.chain(*m)))
                raise UserError(msg)
            auth_document = self.render_authorized_document(auth)
            self.update_document(auth, [access_key, issuing_code])
            attach = self.add_attachment(auth_document, auth)
            # self.send_document(
            #     attachments=[a.id for a in attach],
            #     tmpl='l10n_ec_einvoice.email_template_eretention'
            # )
            return True

    @api.multi
    def print_retention(self):
        return self.env['report'].get_action(
            self,
            'l10n_ec_einvoice.report_eretention'
        )


class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    @api.multi
    def action_generate_eretention(self):
        for obj in self:
            if not obj.journal_id.auth_ret_id.is_electronic:
                return True
            obj.retention_id.action_generate_document()

    @api.multi
    def action_retention_create(self):
        super(AccountInvoice, self).action_retention_create()
        for obj in self:
            if obj.type in ['in_invoice', 'liq_purchase']:
                self.action_generate_eretention()
