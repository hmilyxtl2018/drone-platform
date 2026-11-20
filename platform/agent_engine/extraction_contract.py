"""Select unchanged valid extraction rows; never repair quote text or facts."""
from copy import deepcopy
from jsonschema import Draft202012Validator

def valid_extraction_subset(value,schema,*,allow_overflow=False):
    if not isinstance(value,dict) or set(value)!={'claims'} or not isinstance(value['claims'],list):
        return None
    rows=value['claims'];contract=schema['properties']['claims']
    limit=contract.get('maxItems',16)
    if len(rows)>limit and (not allow_overflow or len(rows)>100):return None
    validator=Draft202012Validator(contract['items'])
    selected=[deepcopy(row) for row in rows if validator.is_valid(row)]
    if allow_overflow and len(selected)>limit:
        # Keep exact rows, sharing the bounded output across source documents.
        # The full rejected output remains saved by the model gateway.
        groups={}
        for row in selected:groups.setdefault(row.get('sourceIndex'),[]).append(row)
        chosen=[]
        while len(chosen)<limit and any(groups.values()):
            for values in groups.values():
                if values and len(chosen)<limit:chosen.append(values.pop(0))
        selected=chosen
    if not selected or len(selected)==len(rows):return None
    result={'claims':selected}
    Draft202012Validator(schema).validate(result)
    return result
