# -*- coding: utf-8 -*-

import base64
import StringIO
from datetime import datetime

from openerp import api, fields, models, _
from openerp.exceptions import Warning as UserError
from openerp.exceptions import ValidationError
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT, DEFAULT_SERVER_DATETIME_FORMAT


from . import utils
from ..xades.sri import SriService
from ..xades.xades import CheckDigit


class AccountEpayment(models.Model):
    _name = 'account.epayment'

    code = fields.Char('Código')
    name = fields.Char('Forma de Pago')


class Edocument(models.AbstractModel):

    _name = 'account.edocument'
    _FIELDS = {
        'account.invoice': 'invoice_number',
        'account.retention': 'name'
    }
    SriServiceObj = SriService()

    clave_acceso = fields.Char(
        'Clave de Acceso',
        readonly=True,
    )
    numero_autorizacion = fields.Char(
        'Número de Autorización',
        readonly=True,
    )
    estado_autorizacion = fields.Char(
        'Estado de Autorización',
        readonly=True,
    )
    fecha_autorizacion = fields.Datetime(
        'Fecha Autorización',
        readonly=True,
    )
    ambiente = fields.Char(
        'Ambiente',
        readonly=True,
    )
    autorizado_sri = fields.Boolean('Authorized?', readonly=True)
    security_code = fields.Char('Security Code', size=8, readonly=True)
    issuing_code = fields.Char('Issuing Code', size=1, readonly=True)
    epayment_id = fields.Many2one('account.epayment', 'Payment Form')
    sent = fields.Boolean('Sent?')
    xml = fields.Text('XML', readonly=True)

    def get_auth(self, document):
        partner = document.company_id.partner_id
        if document._name == 'account.invoice':
            return document.auth_inv_id
        elif document._name == 'account.retention':
            return partner.get_authorisation('ret_in_invoice')

    def get_secuencial(self):
        return getattr(self, self._FIELDS[self._name])[6:]

    def _info_tributaria(self, document, access_key, issuing_code):
        """
        """
        company = document.company_id
        auth = self.get_auth(document)
        infoTributaria = {
            'ambiente': self.env.user.company_id.env_service,
            'tipoEmision': issuing_code,
            'razonSocial': company.name,
            'nombreComercial': company.name,
            'ruc': company.partner_id.vat and company.partner_id.vat[2:] or "9999999999",
            'claveAcceso':  access_key,
            'codDoc': utils.tipoDocumento[auth.type_id.code],
            'estab': auth.entity,
            'ptoEmi': auth.emission_point,
            'secuencial': self.get_secuencial(),
            'dirMatriz': company.street
        }
        return infoTributaria

    def get_code(self):
        code = self.env['ir.sequence'].next_by_code('edocuments.code')
        return code
    
    def _prepare_access_key(self, name):
        if name == 'account.invoice':
            seq = "{:09d}".format(int(self.reference)) 
            auth = self.company_id.partner_id.get_authorisation('out_invoice')
            ld = self.date_invoice.split('-')
            numero = getattr(self, 'invoice_number')
        elif name == 'account.retention':
            auth = self.company_id.partner_id.get_authorisation('ret_in_invoice')  # noqa
            numero = self.get_secuencial()
        return {
            'issuing_date': datetime.strptime(
                self.date, DEFAULT_SERVER_DATE_FORMAT).strftime(
                    "%d%m%Y"
                ),
            'voucher_type': utils.tipoDocumento[auth.type_id.code],
            'vat': self.company_id.partner_id.vat[2:],
            'env_service': self.company_id.env_service,
            # 'series': "{}{}".format(self.auth_inv_id.entity,
                # self.auth_inv_id.emission_point),
            'series': "{}{}".format(auth.entity, auth.emission_point),
            'voucher_number': "{:09d}".format(int(self.reference)),
            'internal_code': "{:08d}".format(0),
            'issuing_type': self.company_id.issuing_code,
        }

    def get_access_key(self, name):
        val = "{issuing_date:4s}" +\
              "{voucher_type:2s}" +\
              "{vat:13s}" +\
              "{env_service:1s}" +\
              "{series:6s}" +\
              "{voucher_number:9s}" +\
              "{internal_code:8s}" +\
              "{issuing_type:1s}"
        access_key = val.format(**self._prepare_access_key(name))
        return "{}{:1d}".format(access_key, CheckDigit.compute_mod11(access_key)) 

    @api.multi
    def _get_codes(self, name='account.invoice'):
        return self.get_access_key(name), self.company_id.issuing_code

    @api.multi
    def check_before_sent(self):
        """
        """
        MESSAGE_SEQUENCIAL = ' '.join([
            u'Los comprobantes electrónicos deberán ser',
            u'enviados al SRI para su autorización en orden cronológico',
            'y secuencial. Por favor enviar primero el',
            ' comprobante inmediatamente anterior.'])
        FIELD = {
            'account.invoice': 'invoice_number',
            'account.retention': 'name'
        }
        number = getattr(self, FIELD[self._name])
        sql = ' '.join([
            "SELECT autorizado_sri, %s FROM %s" % (FIELD[self._name], self._table),  # noqa
            "WHERE state='open' AND %s < '%s'" % (FIELD[self._name], number),  # noqa
            self._name == 'account.invoice' and "AND type = 'out_invoice'" or '',  # noqa
            "ORDER BY %s DESC LIMIT 1" % FIELD[self._name]
        ])
        self.env.cr.execute(sql)
        res = self.env.cr.fetchone()
        if not res:
            return True
        auth, number = res
        if auth is None and number:
            raise UserError(MESSAGE_SEQUENCIAL)
        return True

    def check_date(self, date_invoice):
        """
        Validar que el envío del comprobante electrónico
        se realice dentro de las 24 horas posteriores a su emisión
        """
        LIMIT_TO_SEND = 5
        MESSAGE_TIME_LIMIT = u' '.join([
            u'Los comprobantes electrónicos deben',
            u'enviarse con máximo 24h desde su emisión.']
        )
        dt = datetime.strptime(date_invoice, '%Y-%m-%d')
        days = (datetime.now() - dt).days
        if days > LIMIT_TO_SEND:
            raise UserError(MESSAGE_TIME_LIMIT)

    @api.multi
    def update_document(self, auth, codes):
        fecha = auth.fechaAutorizacion.strftime(DEFAULT_SERVER_DATETIME_FORMAT)
        self.write({
            'numero_autorizacion': auth.numeroAutorizacion,
            'estado_autorizacion': auth.estado,
            'ambiente': auth.ambiente,
            'fecha_autorizacion': fecha,  # noqa
            'autorizado_sri': True,
            'clave_acceso': codes[0],
            'issuing_code': codes[1]
        })

    @api.one
    def add_attachment(self, xml_element, auth):
        buf = StringIO.StringIO()
        buf.write(xml_element.encode('utf-8'))
        document = base64.encodestring(buf.getvalue())
        buf.close()
        attach = self.env['ir.attachment'].create(
            {
                'name': '{0}.xml'.format(self.clave_acceso),
                'datas': document,
                'datas_fname':  '{0}.xml'.format(self.clave_acceso),
                'res_model': self._name,
                'res_id': self.id,
                'type': 'binary'
            },
        )
        return attach

    @api.multi
    def send_document(self, attachments=None, tmpl=False):
        self.ensure_one()
        self._logger.info('Enviando documento electronico por correo')
        tmpl = self.env.ref(tmpl)
        tmpl.send_mail(  # noqa
            self.id,
            email_values={'attachment_ids': attachments}
        )
        self.sent = True
        return True

    def render_document(self, document, access_key, issuing_code):
        pass
