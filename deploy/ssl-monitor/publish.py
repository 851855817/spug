"""Additive publication controller; the adapter owns exact receipts and I/O."""


def publish(host):
    report = {'published': False, 'rolled_back': False, 'maintenance_closed': False}
    baseline = upgraded = None
    maintenance = migration_attempted = ingress_attempted = False
    step = 'preflight'
    try:
        baseline = host.preflight()
        step = 'backup-configuration'
        host.backup_configuration(baseline)
        step = 'maintenance'
        maintenance = True
        host.close()
        report['maintenance_closed'] = True
        host.verify_baseline(baseline)
        step = 'paired-backup'
        host.backup_pair()
        step = 'api-overlay'
        host.overlay_api()
        step = 'additive-migration'
        migration_attempted = True
        host.migrate()
        upgraded = host.verify_additions(baseline)
        step = 'ui-overlay'
        host.overlay_ui()
        host.verify_candidate(baseline, upgraded)
        step = 'future-compose'
        host.compose_candidate()
        step = 'backend-start'
        host.start_candidate()
        host.verify_candidate(baseline, upgraded)
        step = 'open-ingress'
        ingress_attempted = True
        host.open()
        host.verify_open(baseline, upgraded)
        report.update(published=True, maintenance_closed=False)
        host.record(report)
        step = 'resume-health'
        host.resume_health()
        return report
    except BaseException:
        # Exceptions may contain credentials from a database/transport helper.
        report.update(published=False, failure=step)
        if not maintenance:
            report['recovery'] = 'not-required'
            return report

    try:
        host.close()
        report['maintenance_closed'] = True
        if ingress_attempted:
            # A real user could have submitted a new job after ingress opened.
            # Do not downgrade its executor or discard a durable intent.
            host.verify_window(baseline, upgraded)
        if migration_attempted:
            # Complete, recorded additions are the only safe compatibility
            # boundary. Partial DDL stays closed; never generate reverse DDL.
            upgraded = host.verify_additions(baseline)
        host.restore(baseline, compatibility=migration_attempted)
        host.verify_restored(baseline, upgraded, compatibility=migration_attempted)
        host.start_restored(compatibility=migration_attempted)
        host.verify_restored(baseline, upgraded, compatibility=migration_attempted)
        host.open()
        host.verify_old_http()
        host.verify_restored(baseline, upgraded, compatibility=migration_attempted)
        report.update(rolled_back=True, maintenance_closed=False)
        host.record(report)
        host.resume_health()
    except BaseException:
        report.update(rolled_back=False, recovery='verification-required')
        try:
            host.close()
            report['maintenance_closed'] = True
        except BaseException:
            report['maintenance_closed'] = False
        try:
            host.record(report)
        except BaseException:
            pass
    return report
