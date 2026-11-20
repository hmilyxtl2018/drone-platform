"""Read registered documents by ID, retaining exact file and text provenance."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.parse import quote
from urllib.request import url2pathname
import zipfile
from xml.etree import ElementTree


INPUT_SCHEMA = {'type': 'object', 'required': ['arguments'], 'properties': {
    'arguments': {'type': 'object', 'required': ['documentId'], 'properties': {
        'documentId': {'type': 'string', 'minLength': 1, 'maxLength': 200}}, 'additionalProperties': False}},
    'additionalProperties': False}
OUTPUT_SCHEMA = {'type': 'object', 'required': ['status', 'sourceUri', 'contentHash', 'artifactId',
                                             'fileHash', 'segments', 'retrievedAt']}


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def _read_registered_document(source, document_id):
    if source.get('enabled') is not True or source.get('read_only') is not True or source.get('connector') != 'files':
        raise PermissionError('document source must be enabled and read only')
    config = source.get('config_json') or {}
    documents = config.get('documents') or {}
    if not isinstance(document_id, str) or document_id not in documents:
        raise PermissionError('document ID is outside the frozen source scope')
    parts = urlsplit(source.get('root_uri', ''))
    if parts.scheme != 'file' or parts.netloc not in ('', 'localhost') or parts.query or parts.fragment:
        raise ValueError('document root must be a local file URI')
    root = Path(url2pathname(unquote(parts.path))).resolve(strict=True)
    relative = documents[document_id]
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise PermissionError('document location must be a registered relative path')
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise PermissionError('document escapes its registered root')
    max_bytes = config.get('maxBytes', 10 * 1024 * 1024)
    max_chars = config.get('maxTextChars', 200000)
    if type(max_bytes) is not int or not 1 <= max_bytes <= 20 * 1024 * 1024:
        raise ValueError('invalid document byte limit')
    if type(max_chars) is not int or not 1 <= max_chars <= 1000000:
        raise ValueError('invalid document text limit')
    with path.open('rb') as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('document exceeds frozen byte limit')
    suffix = path.suffix.lower()
    chunks = []
    if suffix in {'.md', '.txt', '.csv', '.json'}:
        text = raw.decode('utf-8-sig')
        if suffix == '.json':
            json.loads(text)
        chunks = [(text, {'kind': 'text', 'startLine': 1})]
    elif suffix == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('PDF parser is not installed') from None
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError('encrypted or oversized PDF is unsupported')
        for index, page in enumerate(reader.pages):
            chunks.append((page.extract_text() or '', {'kind': 'pdf-page', 'page': index + 1}))
    elif suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            member = archive.getinfo('word/document.xml')
            if member.file_size > 4 * 1024 * 1024:
                raise ValueError('document XML exceeds parser limit')
            tree = ElementTree.fromstring(archive.read(member))
        namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        for index, paragraph in enumerate(tree.iter(namespace + 'p')):
            chunks.append((''.join(node.text or '' for node in paragraph.iter(namespace + 't')),
                           {'kind': 'docx-paragraph', 'paragraph': index + 1}))
    else:
        raise ValueError('unsupported document format')
    segments, offset = [], 0
    for text, locator in chunks:
        if not text.strip():
            continue
        if offset + len(text) > max_chars:
            raise ValueError('document exceeds frozen text limit')
        segments.append({'text': text, 'contentHash': _hash(text), 'locator': locator,
                         'startOffset': offset, 'endOffset': offset + len(text)})
        offset += len(text) + 2
    body = '\n\n'.join(segment['text'] for segment in segments)
    if not body:
        raise ValueError('document has no extractable text; OCR is required')
    return {'text': body, 'segments': segments, 'fileHash': _hash(raw), 'contentHash': _hash(body),
            'format': suffix.lstrip('.'), 'parserVersion': 'registered-document.v1'}


def read_registered_document(source, document_id):
    try:
        return _read_registered_document(source, document_id)
    except PermissionError as exc:
        if str(exc) in {'document source must be enabled and read only',
                        'document ID is outside the frozen source scope',
                        'document location must be a registered relative path',
                        'document escapes its registered root'}:
            raise
        raise ValueError('document is unavailable in the registered source') from None
    except (FileNotFoundError, IsADirectoryError, OSError):
        raise ValueError('document is unavailable in the registered source') from None
    except (UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise ValueError('document format or encoding is invalid') from None


class DocumentRuntime:
    def __init__(self, repository):
        self.repository = repository

    async def read(self, payload, ctx):
        document_id = payload['arguments']['documentId']
        binding = ctx.profile.tools['document.read']
        source_key = binding.config.get('sourceKey')
        sources = ctx.profile.source_policy.get('documentSources') or []
        selected = [source for source in sources if source.get('source_key') == source_key]
        if len(selected) != 1:
            raise PermissionError('document source is not frozen in this Run')
        try:
            result = await asyncio.to_thread(read_registered_document, selected[0], document_id)
        except (ValueError, PermissionError):
            raise
        except Exception:
            raise ValueError('document parser could not extract bounded text') from None
        now = datetime.now(timezone.utc).isoformat()
        source_uri = 'document://' + quote(source_key, safe='') + '/' + quote(document_id, safe='')
        content = {**result, 'sourceUri': source_uri, 'sourceKey': source_key,
                   'documentId': document_id, 'sourceVersion': selected[0]['policy_version'],
                   'retrievedAt': now, 'truthVerified': False, 'reviewStatus': 'unreviewed'}
        artifact_id = self.repository.add_artifact(ctx.run_id, 'document-source-snapshot', content)
        from agent_engine.connectors.events import observe_source_artifact
        observe_source_artifact(self.repository, artifact_id)
        from agent_engine.evidence_repository import artifact_json_hash
        return {'status': 'read', **content, 'artifactId': artifact_id,
                'artifactHash': artifact_json_hash(content)}


def register_document_tools(engine):
    engine.register_tool('document.read', DocumentRuntime(engine.repo).read, INPUT_SCHEMA, OUTPUT_SCHEMA)
