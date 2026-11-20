"""Bound model material without changing saved public-source snapshots."""
import json


def _wire_bytes(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8')) - 2


def _prefix(value, allowance):
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if _wire_bytes(value[:mid]) <= allowance:
            low = mid
        else:
            high = mid - 1
    return value[:low]


def extraction_material(sources, max_bytes=18000):
    """Share a byte envelope fairly; every omission is recorded, not evidence.

    The provider reserves UTF-8 bytes as an upper token bound. Keep this
    material well below the frozen 60,000-token extraction-node ceiling to
    leave space for the task, schema, skills and goal contract. The gateway
    still enforces all actual budgets and can refuse the final request.
    """
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError('material byte allowance must be nonnegative')
    bodies = [str(s.get('researchPassages') or s.get('body') or s.get('content') or '') for s in sources]
    sizes = [_wire_bytes(body) for body in bodies]
    allowances = [0] * len(sizes)
    remaining, pending = max_bytes, set(range(len(sizes)))
    while pending and remaining:
        share = remaining // len(pending)
        small = [i for i in pending if sizes[i] - allowances[i] <= share]
        if small:
            for i in small:
                cost = sizes[i] - allowances[i]
                allowances[i] += cost
                remaining -= cost
                pending.remove(i)
        else:
            extra = remaining % len(pending)
            for position, i in enumerate(sorted(pending)):
                allowance = min(sizes[i] - allowances[i], share + (position < extra))
                allowances[i] += allowance
                remaining -= allowance
            break
    material, projections = [], []
    for i, source in enumerate(sources):
        body = _prefix(bodies[i], allowances[i])
        material.append({'sourceIndex': i, 'sourceUri': source.get('sourceUri'),
                         'perspectiveKey': source.get('perspectiveKey'), 'body': body})
        projections.append({'sourceIndex': i, 'sourceUri': source.get('sourceUri'),
            'contentHash': source.get('contentHash'), 'availableBytes': sizes[i],
            'selectedBytes': _wire_bytes(body), 'truncated': len(body) < len(bodies[i]),
            'selection': 'ranked-passages' if source.get('researchPassages') else 'source-prefix'})
    return material, {'schemaVersion': 'extraction-material-v1', 'maxBytes': max_bytes,
                      'selectedBytes': sum(p['selectedBytes'] for p in projections),
                      'sources': projections, 'fullSourceSnapshotsPreserved': True}
