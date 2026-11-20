"""Deterministic UAV adapters over explicitly provided records or fixtures."""
from __future__ import annotations

import statistics
from copy import deepcopy
from urllib.parse import quote

from .fixtures import X200_CAPABILITY_INPUT
from .schemas import INPUT_SCHEMA, TOOL_SCHEMAS


def _input(args):
    return args.get('input', {})


def _uav(args):
    data = _input(args)
    if data.get('uavData'):
        return data['uavData']
    if not data.get('fixtureMode'):
        raise ValueError('uavData is required; use fixtureMode=true only for the X200 POC')
    if data.get('uavId', 'X200') != 'X200':
        raise ValueError('the X200 fixture cannot represent another UAV')
    return deepcopy(X200_CAPABILITY_INPUT['uavData'])


def _evidence(args, source, record_id, record=None):
    data = _input(args)
    fixture = bool(data.get('fixtureMode'))
    provenance = {'mode': 'fixture', 'fixtureId': 'uav-x200-demo'} if fixture else {'mode': 'provided-input'}
    item = {'source': source, 'recordId': str(record_id), 'provenance': provenance}
    if fixture:
        item.update(sourceUri=f'fixture://uav-x200-demo/{source}/{quote(str(record_id))}', dataTimestamp='2026-10-19T00:00:00Z')
    elif record:
        for key in ('sourceUri', 'dataTimestamp'):
            if record.get(key):
                item[key] = record[key]
    return item


def _baseline_evidence(args, source, uav):
    data = _input(args)
    if data.get('sourceSnapshot', {}).get('mode') == 'ontology-sqlite':
        return deepcopy(data.get('sourceEvidence', []))
    return [_evidence(args, source, uav.get('uavId'), uav)]


async def get_profile(args, ctx):
    uav = _uav(args)
    return {'uavId': uav.get('uavId'), 'profile': uav, 'evidence': _baseline_evidence(args, 'uav-profile', uav)}


async def get_capability_baseline(args, ctx):
    uav = _uav(args)
    capabilities = {key: uav[key] for key in ('mtowKg', 'payloadKg', 'enduranceHours', 'cruiseSpeedMps', 'maxSpeedMps') if key in uav}
    return {'uavId': uav.get('uavId'), 'capabilities': capabilities, 'evidence': _baseline_evidence(args, 'capability-baseline', uav)}


async def query_flight_logs(args, ctx):
    logs = _input(args).get('flightLogs', [])
    return {'uavId': _uav(args).get('uavId'), 'records': logs, 'recordCount': len(logs), 'evidence': [_evidence(args, 'flight-log', record.get('recordId', i), record) for i, record in enumerate(logs)]}


async def get_mission_summary(args, ctx):
    missions = _input(args).get('missions', [])
    return {'missions': missions, 'missionCount': len(missions), 'evidence': [_evidence(args, 'mission-record', record.get('missionId', i), record) for i, record in enumerate(missions)]}


async def calculate_energy_consumption(args, ctx):
    logs = _input(args).get('flightLogs', [])
    samples = [record for record in logs if record.get('energyWh') is not None]
    durations = [float(record['durationHours']) for record in logs if record.get('durationHours') is not None]
    return {'energyUsedWh': sum(float(record['energyWh']) for record in samples) if samples else None,
            'averageDurationHours': statistics.mean(durations) if durations else None, 'sampleCount': len(samples),
            'evidence': [_evidence(args, 'flight-log', record.get('recordId', i), record) for i, record in enumerate(logs) if record.get('energyWh') is not None]}


async def get_battery_health(args, ctx):
    uav = _uav(args)
    history = _input(args).get('batteryHistory', {})
    soh = history.get('soh', uav.get('batterySoh'))
    return {'soh': soh, 'cycleCount': history.get('cycleCount'),
            'risk': 'unknown' if soh is None else 'medium' if float(soh) < .85 else 'low',
            'evidence': [_evidence(args, 'battery-history', uav.get('uavId'), history or uav)] if soh is not None else []}


def _checks(args):
    uav, requirements = _uav(args), _input(args).get('requirements', {})
    checks, unknown, gaps = {}, [], []
    for key, required in requirements.items():
        if key not in {'enduranceHours', 'payloadKg', 'cruiseSpeedMps'} or uav.get(key) is None:
            unknown.append(key)
            continue
        actual = float(uav[key])
        checks[key] = actual >= float(required)
        if not checks[key]:
            gaps.append({'metric': key, 'required': required, 'actual': actual, 'gap': round(float(required)-actual, 8)})
    return checks, unknown, gaps


async def check_requirement_compliance(args, ctx):
    checks, unknown, _ = _checks(args)
    uav = _uav(args)
    return {'checks': checks, 'compliant': bool(checks) and not unknown and all(checks.values()), 'unknownRequirements': unknown,
            'comparisonBasis': 'fixture-values' if _input(args).get('fixtureMode') else 'reported-values' if _input(args).get('sourceSnapshot') else 'provided-values',
            'provisional': True,
            # Numeric comparisons alone do not certify requirement compliance.
            'verifiedCompliance': None,
            'evidence': _baseline_evidence(args, 'capability-baseline', uav)}


async def find_capability_gap(args, ctx):
    _, unknown, gaps = _checks(args)
    uav = _uav(args)
    return {'gaps': gaps, 'hasGap': bool(gaps), 'unknownRequirements': unknown,
            'evidence': _baseline_evidence(args, 'capability-baseline', uav)}


def register_uav_tools(engine):
    handlers = {'uav.get_profile': get_profile, 'uav.get_capability_baseline': get_capability_baseline,
                'uav.query_flight_logs': query_flight_logs, 'uav.get_mission_summary': get_mission_summary,
                'uav.calculate_energy_consumption': calculate_energy_consumption, 'uav.get_battery_health': get_battery_health,
                'uav.check_requirement_compliance': check_requirement_compliance, 'uav.find_capability_gap': find_capability_gap}
    for key, handler in handlers.items():
        engine.register_tool(key, handler, INPUT_SCHEMA, TOOL_SCHEMAS[key])
