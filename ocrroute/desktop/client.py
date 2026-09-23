# coding=utf-8
"""Thin synchronous HTTP client for the /v1 API. Used by every desktop worker; never called on the GUI thread."""
from __future__ import absolute_import, division, print_function

import json
from pathlib import Path

import requests


class ApiError(Exception):
    def __init__(self, status, code, message):
        super(ApiError, self).__init__('{}: {}'.format(code, message))
        self.status, self.code, self.message = status, code, message


class OcrRouteClient(object):
    def __init__(self, base_url, api_key='', timeout=180.0):
        self.__m_baseUrl = base_url.rstrip('/')
        self.__m_apiKey = api_key
        self.__m_timeout = timeout
        self.__m_s = requests.Session()

    def _headers(self):
        return (
            {'Authorization': 'Bearer {}'.format(self.__m_apiKey), 'User-Agent': 'OcrRoute-Desktop/0.1'}
            if self.__m_apiKey
            else {}
        )

    def _req(self, method, path, **kw):
        kw.setdefault('timeout', self.__m_timeout)
        r = self.__m_s.request(
            method, '{}{}'.format(self.__m_baseUrl, path), headers=dict(self._headers(), **kw.pop('headers', {})), **kw
        )
        ct = r.headers.get('content-type', '')
        data = r.json() if ct.startswith('application/json') else r.content
        if r.status_code >= 400 and not (isinstance(data, dict) and 'run_id' in data):
            code = data.get('error_code', 'http_error') if isinstance(data, dict) else 'http_error'
            msg = data.get('error_message', r.text[:200]) if isinstance(data, dict) else r.text[:200]
            raise ApiError(r.status_code, code, msg)
        return data

    # system
    def health(self):
        return self._req('GET', '/v1/health')

    def ready(self):
        return self._req('GET', '/v1/ready')

    def version(self):
        return self._req('GET', '/v1/version')

    def doctor(self):
        return self._req('GET', '/v1/doctor')

    def doctorMarkdown(self):
        return self._req('GET', '/v1/doctor?format=markdown').decode()

    # ocr
    def ocrFile(self, path, **options):
        with open(path, 'rb') as fh:
            return self.ocrBytes(fh.read(), Path(path).name, **options)

    def ocrBytes(self, data, filename='image.png', **options):
        opts = {k: v for k, v in options.items() if v not in (None, '', [], {})}
        return self._req('POST', '/v1/ocr', files={'file': (filename, data)}, data={'json': json.dumps(opts)})

    def artifact(self, run_id, kind):
        return self._req('GET', '/v1/runs/{}/artifacts/{}'.format(run_id, kind))

    # catalogue / config
    def engines(self):
        return self._req('GET', '/v1/engines')['items']

    def refreshEngines(self):
        return self._req('POST', '/v1/engines/refresh')

    def patchEngine(self, engine_id, **fields):
        return self._req('PATCH', '/v1/engines/{}'.format(engine_id), json=fields)

    def probeEngine(self, engine_id):
        return self._req('POST', '/v1/engines/{}/probe'.format(engine_id))

    def providers(self):
        return self._req('GET', '/v1/providers')['items']

    def createProvider(self, **body):
        return self._req('POST', '/v1/providers', json=body)

    def patchProvider(self, pid, **body):
        return self._req('PATCH', '/v1/providers/{}'.format(pid), json=body)

    def deleteProvider(self, pid):
        self._req('DELETE', '/v1/providers/{}'.format(pid))

    def testProvider(self, pid):
        return self._req('POST', '/v1/providers/{}/test'.format(pid))

    def resetCircuit(self, pid):
        return self._req('POST', '/v1/providers/{}/reset-circuit'.format(pid))

    def createCredential(self, provider_id, secret, alias=''):
        return self._req('POST', '/v1/credentials', json={'provider_id': provider_id, 'secret': secret, 'alias': alias})

    def deleteCredential(self, cid):
        self._req('DELETE', '/v1/credentials/{}'.format(cid))

    def verifyCredential(self, cid):
        return self._req('POST', '/v1/credentials/{}/verify'.format(cid))

    def routes(self):
        return self._req('GET', '/v1/routes')

    def createRoute(self, **body):
        return self._req('POST', '/v1/routes', json=body)

    def patchRoute(self, rid, **body):
        return self._req('PATCH', '/v1/routes/{}'.format(rid), json=body)

    def deleteRoute(self, rid):
        self._req('DELETE', '/v1/routes/{}'.format(rid))

    def simulate(self, **body):
        return self._req('POST', '/v1/routes/simulate', json=body)

    def keys(self):
        return self._req('GET', '/v1/keys')['items']

    def createKey(self, **body):
        return self._req('POST', '/v1/keys', json=body)

    def revokeKey(self, kid):
        self._req('DELETE', '/v1/keys/{}'.format(kid))

    # runs / stats / jobs
    def runs(self, **params):
        return self._req('GET', '/v1/runs', params=params)['items']

    def run(self, run_id):
        return self._req('GET', '/v1/runs/{}'.format(run_id))

    def deleteRun(self, run_id):
        self._req('DELETE', '/v1/runs/{}'.format(run_id))

    def stats(self, hours=24):
        return self._req('GET', '/v1/stats/summary', params={'hours': hours})

    def usage(self, group_by='engine'):
        return self._req('GET', '/v1/usage', params={'group_by': group_by})['items']

    def createBatch(self, files, **spec):
        fh = [('files', (f.name, open(f, 'rb'))) for f in files]
        try:
            return self._req('POST', '/v1/batch', files=fh, data={'body': json.dumps(spec)})
        finally:
            for _n, (_fn, h) in fh:
                h.close()

    def jobs(self):
        return self._req('GET', '/v1/jobs')['items']

    def job(self, job_id):
        return self._req('GET', '/v1/jobs/{}'.format(job_id))

    def cancelJob(self, job_id):
        self._req('POST', '/v1/jobs/{}/cancel'.format(job_id))

    def settings(self):
        return self._req('GET', '/v1/settings')

    def patchSettings(self, values):
        return self._req('PATCH', '/v1/settings', json={'values': values})

    def tools(self):
        return self._req('GET', '/v1/tools')

    def getBaseUrl(self):
        """
        :return: any
        """
        return self.__m_baseUrl

    def setBaseUrl(self, baseUrl):
        """
        :param baseUrl: any
        """
        self.__m_baseUrl = baseUrl

    def getApiKey(self):
        """
        :return: any
        """
        return self.__m_apiKey

    def setApiKey(self, apiKey):
        """
        :param apiKey: any
        """
        self.__m_apiKey = apiKey

    def getTimeout(self):
        """
        :return: any
        """
        return self.__m_timeout

    def setTimeout(self, timeout):
        """
        :param timeout: any
        """
        self.__m_timeout = timeout

    def getSession(self):
        """
        :return: requests.Session - the underlying HTTP session (tests swap in an in-process client)
        """
        return self.__m_s

    def setSession(self, session):
        """
        :param session: requests.Session
        """
        self.__m_s = session

    session = property(fget=getSession, fset=setSession)
    base_url = property(fget=getBaseUrl, fset=setBaseUrl)
    api_key = property(fget=getApiKey, fset=setApiKey)
    timeout = property(fget=getTimeout, fset=setTimeout)
