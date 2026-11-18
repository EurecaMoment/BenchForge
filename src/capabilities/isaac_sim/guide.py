"""Model-readable tool usage joined with the simulator's live public discovery."""


def usage(capabilities, catalog, *, operation=None, feature_id=None):
    """Keep live schemas authoritative and distinguish discovery from acceptance."""
    operations = capabilities.get('capabilities', [])
    features = [feature for domain in catalog.get('domains', [])
                for feature in domain.get('features', [])]
    if operation:
        operations = [row for row in operations if row['name'] == operation]
        if not operations:
            raise ValueError('Operation is absent from the live Isaac Sim capabilities')
    if feature_id:
        features = [row for row in features if row['id'] == feature_id]
        if not features:
            raise ValueError('Feature is absent from the live Isaac Sim catalog')
    return {
        'schema': 'benchforge.isaac-sim-guide/v1',
        'service': 'a100sim.v1.Simulator',
        'capability_source': 'Live GetCapabilities and GetApiCatalog responses; no repository inspection',
        'rules': [
            'Use public capability and catalog responses to select operations. Do not inspect Git, '
            'deployment directories or internal service ports to infer runtime support.',
            'AVAILABLE describes current routing/readiness, not acceptance of this task. UNVERIFIED '
            'and PARTIAL require checking the stated scope; UNAVAILABLE/UNSUPPORTED are not promises.',
            'Use the exact published parameter_schema or request_schema. A schema without properties '
            'does not document its fields: do not infer either support or rejection from missing properties.',
            'An extension capability name starts with extension.; SubmitTask uses the suffix in '
            'request.extension.operation. InvokeFeature takes a catalog feature id, not an operation name.',
            'Submit/invoke return accepted work. Observe the same job until SUCCEEDED, then download '
            'its manifest artifacts. Failure and timeout are not completed work or accepted data.',
            'Retain request_id and payload when recovering a lost submission reply. A changed payload '
            'needs a new request_id. wait never resubmits; cancellation needs service confirmation.',
            'Session identifiers belong to the operation family that created them. Do not reuse an '
            'identifier across unrelated scene/robot/world backends without an explicit service contract.',
            'Keep task code and uploads in this conversation workspace. Use returned source_path, '
            'asset_id, relative_path and job_id verbatim. Stage and quality reviews still apply.',
        ],
        'actions': {
            'guide': 'Read this guide and live summaries; operation or feature_id selects full live details.',
            'capabilities': 'GetCapabilities: current operations, availability, scope and published parameter schemas.',
            'catalog': 'GetApiCatalog: feature ids, support, availability and request schemas; filter with feature_id.',
            'assets': 'ListAssets: uploaded asset identities and their relative paths.',
            'upload': 'UploadAsset: source_path is an existing file inside this workspace; retain the returned handle.',
            'submit': 'SubmitTask: request is the native task object without request_id.',
            'invoke': 'InvokeFeature: feature_id is from the live catalog; request is its arguments object.',
            'status': 'GetJob: job_id must belong to this conversation and configured endpoint.',
            'wait': 'WatchJob/GetJob: observe job_id for timeout_seconds in (0, 60], without resubmission.',
            'cancel': 'CancelJob: a running GPU job can refuse cancellation; inspect the returned state.',
            'download': 'DownloadArtifact: only SUCCEEDED job-manifest artifacts are copied into this workspace.',
        },
        'example_note': 'Placeholders must be replaced with returned identities and the selected live schema. '
                        'Empty argument objects below are structural templates, not ready-to-submit requests.',
        'examples': [
            {'action': 'guide'},
            {'action': 'guide', 'operation': 'extension.asset.import'},
            {'action': 'catalog', 'feature_id': '<id returned by catalog>'},
            {'action': 'upload', 'source_path': 'tasks/example/scene.usda'},
            {'action': 'submit', 'request_id': '<stable identity for this payload>',
             'request': {'extension': {'operation': '<published operation without extension.>',
                                       'arguments': {}}}},
            {'action': 'invoke', 'feature_id': '<id returned by catalog>',
             'request': {}},
            {'action': 'wait', 'job_id': '<returned job_id>', 'timeout_seconds': 30},
            {'action': 'download', 'job_id': '<succeeded job_id>'},
        ],
        'live': {
            'api_version': capabilities.get('api_version'),
            'operations': operations if operation else [
                {key: row[key] for key in ('name', 'availability', 'scope', 'reason') if key in row}
                for row in operations],
            'features': features if feature_id else [],
            'feature_domains': [] if feature_id else [
                {'id': domain['id'], 'title': domain.get('title', ''),
                 'feature_count': len(domain.get('features', []))} for domain in catalog.get('domains', [])],
        },
    }
