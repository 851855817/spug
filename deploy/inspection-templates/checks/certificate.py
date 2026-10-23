def main():
    domain = domain_name(os.environ.get('_SPUG_DOMAIN', ''))
    with connect_tls(domain) as conn:
        certificate = conn.getpeercert()
        expiry = ssl.cert_time_to_seconds(certificate['notAfter'])
    remaining = (expiry - time.time()) / 86400
    emit('%s | chain/hostname verified | expires UTC %s | %.1f days left' % (
        domain, datetime.datetime.fromtimestamp(expiry, datetime.timezone.utc).isoformat(), remaining))
    if remaining < 30:
        emit('WARNING: less than 30 days remaining; this template does not renew certificates.')
        return 1
    emit('Certificate expiry check passed (30-day threshold); not a full TLS configuration audit.')
