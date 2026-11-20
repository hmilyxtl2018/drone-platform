"""Audited adapters for versioned, read-only professional research methods."""
from copy import deepcopy
from pathlib import Path
import asyncio
import json
import os

from jsonschema import validate
import research_methods as methods

VERSION = '1.0.17'
TREND_VERSION = '1.3.17'
SUPPORTED_RESEARCH_VERSIONS = frozenset({'1.0.0','1.0.1','1.0.2','1.0.3','1.0.4','1.0.5','1.0.6','1.0.7','1.0.8','1.0.9','1.0.10','1.0.11','1.0.12','1.0.13','1.0.14','1.0.15','1.0.16',VERSION})
SUPPORTED_TREND_VERSIONS = frozenset({'1.3.0','1.3.1','1.3.2','1.3.3','1.3.4','1.3.5','1.3.6','1.3.7','1.3.8','1.3.9','1.3.10','1.3.11','1.3.12','1.3.13','1.3.14','1.3.15','1.3.16',TREND_VERSION})
OBJ = {'type':'object'}
ROWS = {'type':'array','maxItems':100,'items':OBJ}
TEXT = {'type':'string','maxLength':4000}
PROPERTIES = {
    'analysis.source_dependencies': {'sources':ROWS},
    'analysis.entity_candidates': {'mentions':ROWS,'registry':{'type':'array','maxItems':2000,'items':OBJ}},
    'analysis.series_build': {'observations':ROWS},
    'analysis.claim_relation': {'claims':ROWS},
    'analysis.evidence_rank': {'query':TEXT,'sources':ROWS},
    'analysis.evidence_coverage': {'questions':ROWS,'evidence':ROWS,'findings':ROWS,'searches':ROWS},
    'analysis.ontology_validate': {'ontology':OBJ},
    'analysis.hypothesis_compare': {'hypotheses':ROWS,'assessments':ROWS,'evidence':ROWS,'dependencyGroups':ROWS},
    'analysis.scenario_consistency': {'scenarios':ROWS,'constraints':ROWS,'evidence':ROWS},
    'analysis.robustness_check': {'findings':ROWS,'evidence':ROWS,'dependencyGroups':ROWS,'series':ROWS,'parameters':OBJ},
    'analysis.maturity_assess': {'objects':ROWS,'criteria':OBJ,'evidence':ROWS},
    'analysis.method_evaluate': {'cases':ROWS,'baselineVersion':TEXT,'candidateVersion':TEXT},
}
CHANGE_PROPERTIES = {'series':ROWS,'parameters':OBJ}
ARGUMENT_SCHEMAS = {k:{'type':'object','properties':v,'required':list(v),'additionalProperties':False} for k,v in PROPERTIES.items()}
CHANGE_ARGUMENT_SCHEMA = {'type':'object','properties':CHANGE_PROPERTIES,'required':['series','parameters'],'additionalProperties':False}
INPUT_SCHEMAS = {k:{'type':'object','required':['arguments'],'properties':{'arguments':s},'additionalProperties':False} for k,s in ARGUMENT_SCHEMAS.items()}
CHANGE_INPUT_SCHEMA = {'type':'object','required':['arguments'],'properties':{'arguments':CHANGE_ARGUMENT_SCHEMA},'additionalProperties':False}
OUTPUT_SCHEMA = {'type':'object','required':['method','methodVersion','analysisStatus','applicability','sampleCount','missingInputs','reasonCodes','truthVerified','provenance','artifactId','artifactHash'],
    'properties':{'method':TEXT,'methodVersion':TEXT,
        'analysisStatus':{'enum':['assessed','partial','not-applicable','invalid-input']},
        'applicability':{'enum':['applicable','limited','inapplicable']},'sampleCount':{'type':'integer','minimum':0},
        'missingInputs':{'type':'array','items':TEXT},'reasonCodes':{'type':'array','items':TEXT},
        'truthVerified':{'const':False},'provenance':OBJ,'artifactId':TEXT,'artifactHash':TEXT},'additionalProperties':True}


def implementation_hash():
    root = Path(__file__).resolve().parents[2]
    paths = [root/'platform/research_methods.py', Path(__file__),root/'platform/agent_tools/future_battlefield.py',root/'tools/validate_report_ontology.py',
        root/'ontology/research-report/report-shapes.ttl',root/'ontology/research-report/report-ontology.ttl',
        root/'ontology/coast-raid-k/semantic_validation.py',root/'tools/semantics-requirements.lock.txt',
        root/'platform/agent_engine/future_battlefield_runtime.py',root/'platform/agent_engine/research_reasoning.py',
        root/'platform/agent_engine/extraction_contract.py',
        root/'platform/agent_engine/decision_semantics.py',root/'platform/agent_engine/decision_reasoning.py',
        root/'platform/agent_engine/decision_material.py',
        root/'platform/agent_engine/future_report.py',root/'platform/agent_engine/validation.py',
        root/'platform/agent_engine/reasoning_repository.py',
        root/'platform/agent_engine/reasoning_gates.py',
        root/'platform/agent_engine/executor.py',root/'platform/agent_engine/engine.py',
        root/'platform/agent_engine/data_contracts.py',root/'platform/agent_engine/task_contracts.py',
        root/'platform/agent_engine/task_intent.py',
        root/'platform/agent_engine/orchestration.py',root/'platform/agent_engine/phase3_repository.py']
    paths.append(root/'platform/agent_engine/execution_baseline.py')
    paths.extend([root/'platform/agent_engine/research.py', root/'platform/future_battlefield_algorithms.py'])
    paths.extend([root/'platform/agent_engine/research_reflection.py', root/'platform/agent_engine/research_candidate.py'])
    return methods.digest({str(p.relative_to(root)):__import__('hashlib').sha256(p.read_bytes()).hexdigest() for p in paths})


def calculate(key, args):
    if key == 'analysis.source_dependencies': return methods.source_dependencies(args['sources'])
    if key == 'analysis.entity_candidates': return methods.entity_candidates(args['mentions'],args['registry'])
    if key == 'analysis.series_build': return methods.series_build(args['observations'])
    if key == 'analysis.change_point': return methods.cusum(args['series'],args['parameters'])
    if key == 'analysis.claim_relation': return methods.claim_relations(args['claims'])
    if key == 'analysis.evidence_rank': return methods.rank_evidence(args['query'],args['sources'])
    if key == 'analysis.evidence_coverage': return methods.coverage(args['questions'],args['evidence'],args['findings'],args['searches'])
    if key == 'analysis.hypothesis_compare': return methods.hypothesis_compare(args['hypotheses'],args['assessments'],args['evidence'],args.get('dependencyGroups',[]))
    if key == 'analysis.scenario_consistency':
        ids = {str(methods.evidence_id(e)) for e in args['evidence'] if methods.evidence_id(e)}
        constraints = [c for c in args['constraints'] if c.get('evidenceIds') and set(c['evidenceIds']).issubset(ids)]
        return methods.scenario_consistency(args['scenarios'],constraints)
    if key == 'analysis.robustness_check': return methods.robustness(args['findings'],args['evidence'],args['dependencyGroups'],args['series'],args['parameters'])
    if key == 'analysis.maturity_assess': return methods.maturity(args['objects'],args['criteria'],args['evidence'])
    if key == 'analysis.method_evaluate': return methods.evaluate_methods(args['cases'],args['baselineVersion'],args['candidateVersion'])
    raise ValueError('unsupported research method')


async def ontology_validate(ontology):
    root = Path(__file__).resolve().parents[2]
    # Isolated, dependency-locked formal engine; no arbitrary command/URL is accepted.
    python = root/'.venv-semantics'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        return methods.result('shacl-and-owlrl-report-constraints',{},missing=['locked-semantic-runtime'])
    process = await asyncio.create_subprocess_exec(str(python),str(root/'tools/validate_report_ontology.py'),
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,cwd=str(root))
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(ontology).encode()),timeout=45)
    except BaseException:
        process.kill(); await process.wait(); raise
    if process.returncode:
        raise RuntimeError('formal report validator failed: '+stderr.decode(errors='replace')[-1000:])
    return json.loads(stdout)


class ResearchTools:
    def __init__(self,repository): self.repository=repository

    async def invoke(self,key,payload,ctx):
        schema = CHANGE_INPUT_SCHEMA if key=='analysis.change_point' else INPUT_SCHEMAS[key]
        validate(payload,schema)
        if len(json.dumps(payload,allow_nan=False).encode()) > 4_000_000: raise ValueError('research input exceeds 4 MB')
        binding=ctx.profile.tools[key]
        expected=SUPPORTED_TREND_VERSIONS if key=='analysis.change_point' else SUPPORTED_RESEARCH_VERSIONS
        if binding.version not in expected: raise ValueError('unsupported frozen research method version')
        actual=implementation_hash()
        frozen=binding.config.get('implementationHash')
        if not frozen or frozen!=actual: raise ValueError('research implementation differs from frozen binding; publish and validate successor')
        args=deepcopy(payload['arguments'])
        if key=='analysis.maturity_assess' and args['criteria'].get('sourceArtifactId'):
            criteria=args['criteria']; artifact=self.repository.get_artifact(ctx.run_id,criteria['sourceArtifactId'])
            if not artifact or artifact['content_hash']!=criteria.get('contentHash'):
                raise ValueError('maturity standard must reference an exact saved artifact in this Run')
        value=await ontology_validate(args['ontology']) if key=='analysis.ontology_validate' else calculate(key,args)
        value['provenance']={'runId':ctx.run_id,'toolKey':key,'version':binding.version,'inputHash':methods.digest(args),
            'implementationHash':actual,'inputAuthority':'run-material','reviewStatus':'unverified'}
        artifact=self.repository.add_artifact(ctx.run_id,'analysis-result',value)
        return {**value,'artifactId':artifact,'artifactHash':methods.digest(value)}


def register_research_tools(engine):
    runtime=ResearchTools(engine.repo)
    for key in PROPERTIES:
        async def handler(payload,ctx,key=key): return await runtime.invoke(key,payload,ctx)
        engine.register_tool(key,handler,deepcopy(INPUT_SCHEMAS[key]),deepcopy(OUTPUT_SCHEMA))
    return runtime
