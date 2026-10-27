"""Private provider-submission journal for safe extraction-job recovery.

Uses existing audit storage. It contains identifiers and source hashes only,
never credentials, signed URLs or source contents.
"""
import hashlib
import json

from app.models import AuditLog

EVENT = 'GROUNDX_SUBMISSION_CHECKPOINT'


class GroundXSubmissionUncertain(RuntimeError):
    """A provider submission may exist, so resubmission needs investigation."""


def source_binding(file_bytes, file_name, form_type, bucket_id):
    return {'source_sha256': hashlib.sha256(file_bytes).hexdigest(),
            'file_name': file_name, 'form_type': form_type.value, 'bucket_id': str(bucket_id)}


def binding_key(binding):
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


async def job_checkpoint_journal(repo, filing_id, job_id, rule_version):
    """Load this job's current-rule journal and return its durable recorder."""
    entries = {}
    # Repository implementations differ in log ordering; explicit time order
    # prevents an older SUBMITTING record replacing its accepted process ID.
    logs = await repo.list_audit_logs(filing_id)
    for log in sorted(logs, key=lambda item: item.created_at):
        details = log.details or {}
        if (log.event != EVENT or details.get('job_id') != job_id
                or not isinstance(details.get('binding'), dict)):
            continue
        if details.get('rule_version') != rule_version:
            raise GroundXSubmissionUncertain('The published rules changed since this provider submission. Verify the existing process before resuming this job.')
        entries[binding_key(details['binding'])] = details

    async def record(binding, status, process_id=None):
        details = {'job_id': job_id, 'rule_version': rule_version, 'binding': binding,
                   'status': status, 'process_id': process_id}
        await repo.add_audit(AuditLog(filing_id=filing_id, event=EVENT,
            message='Provider extraction submission checkpoint.', details=details))
        entries[binding_key(binding)] = details

    return entries, record
