"""Account permissions publication; every rollback keeps maintenance closed."""


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
        step = 'audit-rotation'
        host.install_rotation()
        step = 'api-overlay'
        host.overlay_api()
        step = 'additive-migration'
        migration_attempted = True
        host.migrate()
        upgraded = host.verify_additions(baseline)
        step = 'explicit-legacy-initialization'
        upgraded = host.initialize(baseline, upgraded)
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
        step = 'resume-health'
        host.resume_health()
        report.update(published=True, maintenance_closed=False)
        step = 'record'
        host.record(report)
        return report
    except BaseException:
        report.update(published=False, failure=step)
        if not maintenance:
            report['recovery'] = 'not-required'
            return report
    try:
        host.close()
        report['maintenance_closed'] = True
        if ingress_attempted:
            host.verify_window(baseline, upgraded)
        if migration_attempted:
            # Partial DDL or unverified initialization must never be downgraded.
            upgraded = host.verify_additions(baseline)
        host.restore(baseline, compatibility=migration_attempted)
        host.verify_restored(baseline, upgraded, compatibility=migration_attempted)
        report.update(rolled_back=True, recovery='maintenance-retained')
        # Old code lacks isolation. Deliberately no backend start, ingress open,
        # or health resumption on this path, including later container restarts.
        host.record(report)
    except BaseException:
        report.update(rolled_back=False, recovery='verification-required')
        try:
            host.close()
            report['maintenance_closed'] = True
        except BaseException:
            report['maintenance_closed'] = False
        try: host.record(report)
        except BaseException: pass
    return report
