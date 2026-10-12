from __future__ import annotations
import difflib
from typing import Any
import pandas as pd
from .metrics import calculate_metrics

def compare_results(results:list[dict[str,Any]])->pd.DataFrame:return pd.DataFrame(calculate_metrics(x) for x in results)
def text_similarity(a:str,b:str)->float:
    a=" ".join((a or "").split()); b=" ".join((b or "").split())
    return 100.0 if not a and not b else round(difflib.SequenceMatcher(None,a,b,autojunk=False).ratio()*100,2)
def similarity_matrix(results:list[dict[str,Any]])->pd.DataFrame:
    names=[x.get("display_name",x.get("parser","Unknown")) for x in results]
    return pd.DataFrame([[text_similarity(a.get("markdown",""),b.get("markdown","")) for b in results] for a in results],index=names,columns=names)
