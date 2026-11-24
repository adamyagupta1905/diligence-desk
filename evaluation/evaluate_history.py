"""Explicit live check for the fictional three-year statement pack (four requests).

From the repository root:
    .venv/bin/python evaluation/evaluate_history.py --live
No requests occur without --live. Does not create a case or approve any records.
"""
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so "deal_screening" imports work from any folder

from dotenv import load_dotenv
from deal_screening.documents import DocumentReader
from deal_screening.ledger import LedgerValidator, LedgerDiagnostics
from deal_screening.ledger_extraction import LedgerExtractor
from deal_screening.llm import GeminiClient, safe_api_error



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    if not args.live:parser.error('Supply --live to authorize four Gemini requests; use app.py --check for offline tests.')
    load_dotenv(ROOT / '.env')
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key:parser.error('Set GEMINI_API_KEY in .env.')
    model=os.getenv('GEMINI_MODEL','gemini-3.8-flash')
    source=ROOT/'sample_documents/13_three_year_statements.txt';data=source.read_bytes()
    chunks=DocumentReader.chunks(DocumentReader.read(data,source.name))
    extractor=LedgerExtractor(GeminiClient(key))
    result={'source_sha256':hashlib.sha256(data).hexdigest(),'model':model,'rows':[],'retrieval':{}}
    destination=ROOT/'tmp/history_live_check.json';destination.parent.mkdir(exist_ok=True)
    def save():destination.write_text(json.dumps(result,indent=2))
    try:
        print('Discovering annual historical periods…',flush=True)
        context=extractor.discover(chunks,model);result['context']=context;save()
        print('Discovered years:',[p['fiscal_year'] for p in context['periods']],flush=True)
        for statement in ('IS','BS','CF'):
            print('Extracting '+statement+'…',flush=True)
            rows,passages=extractor.statement(chunks,context,statement,model)
            result['rows']+=rows;result['retrieval'][statement]=passages;save()
            print(statement+': '+str(len(rows))+' candidates',flush=True)
        errors,normalized=LedgerValidator.validate(context,result['rows'],chunks)
        checks,derived=LedgerDiagnostics.analyze(normalized,context['periods'])
        expected=json.loads((ROOT/'tests/ledger_fixture.json').read_text())
        expected_rows=LedgerValidator.validate(expected['context'],expected['rows'],chunks)[1]
        lookup=lambda rows:{(r['fiscal_year'],r['metric']):Decimal(r['normalized_value']) for r in rows}
        actual,wanted=lookup(normalized),lookup(expected_rows)
        differences=[f'{key}: got {actual.get(key)}, expected {value}' for key,value in wanted.items() if actual.get(key)!=value]
        differences += ['Unexpected reported metric: '+str(key) for key in actual.keys()-wanted.keys()]
        if {p['fiscal_year'] for p in context['periods']}!={2023,2024,2025}:
            differences.append('Historical fiscal-year discovery does not match the source.')
        result.update(validation_errors=errors,checks=checks,derived=derived,differences=differences)
        save()
        print('Normalization/evidence errors:',errors,flush=True)
        print('Known-answer differences:',differences,flush=True)
        print('Arithmetic checks:',{status:sum(c['status']==status for c in checks) for status in ('pass','fail','unavailable')},flush=True)
        passed=not errors and not differences and all(c['status']=='pass' for c in checks)
        print('PASS' if passed else 'FAIL',flush=True)
        print('Diagnostic JSON:',destination,flush=True)
        print('No saved case or analyst approval was created.',flush=True)
        return 0 if passed else 1
    except Exception as exc:
        result['error']=safe_api_error(exc);save()
        print('Live evaluation failed:',result['error'],flush=True)
        return 1


if __name__=='__main__':raise SystemExit(main())
