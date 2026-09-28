from __future__ import annotations
import re
from pathlib import Path
from typing import Any

from services.usage_tracker import combine_usage_reports

# Stage keys are consumed by the UI as well, so keep them in one place.
USAGE_STAGES = ("vlm_ocr", "extraction")


def result_usage(result:dict[str,Any])->dict[str,Any]:
    """Return the per-stage usage tree for one parser result.

    Older results have no ``usage`` key, so the extraction-level report is
    read from ``json_data`` as a fallback to keep the benchmark table useful
    for previously saved runs.
    """
    usage=result.get("usage")
    if isinstance(usage,dict) and usage:
        return usage
    extraction=((result.get("json_data") or {}).get("information_extraction") or {})
    legacy=extraction.get("usage")
    return {"extraction": legacy} if isinstance(legacy,dict) and legacy else {}


def usage_stage_reports(result:dict[str,Any])->list[dict[str,Any]]:
    usage=result_usage(result)
    reports=[]
    for stage in USAGE_STAGES:
        report=usage.get(stage)
        if isinstance(report,dict):
            reports.append(report)
    icd=usage.get("icd_suggestion")
    if isinstance(icd,dict):
        reports.append(icd)
    return reports


def calculate_metrics(result:dict[str,Any])->dict[str,Any]:
    md=result.get("markdown") or ""; data=result.get("json_data") or {}; directory=result.get("artifacts",{}).get("output_directory")
    table_lines=len(re.findall(r"(?m)^\s*\|(?:.*\|)+\s*$",md))//2
    total=combine_usage_reports(usage_stage_reports(result),stage="total")
    vlm=(result_usage(result).get("vlm_ocr") or {})
    return {"Parser":result.get("parser","Unknown"),"Status":result.get("status","unknown"),
    "Time (s)":result.get("performance",{}).get("processing_seconds",0),"Characters":len(md),
    "Words":len(re.findall(r"\b\w+\b",md,flags=re.UNICODE)),"Headings":len(re.findall(r"(?m)^#{1,6}\s+",md)),
    "Tables (estimated)":max(table_lines,_labels(data,{"table"})),"Images (estimated)":_labels(data,{"image","picture","figure"}),
    "JSON nodes":_nodes(data),"Output bytes":_size(Path(directory)) if directory else 0,"Error":result.get("error") or "",
    "VLM input tokens":vlm.get("input_tokens",0),"VLM output tokens":vlm.get("output_tokens",0),
    "Input tokens":total.get("input_tokens",0),"Output tokens":total.get("output_tokens",0),
    "Total tokens":total.get("total_tokens",0),"LLM requests":total.get("requests",0),
    "Cost (USD)":round(float(total.get("cost",{}).get("usd",0.0)),6),
    "Usage source":total.get("source","none")}

def _nodes(v):
    if isinstance(v,dict):return 1+sum(_nodes(x) for x in v.values())
    if isinstance(v,list):return 1+sum(_nodes(x) for x in v)
    return 1

def _labels(v,labels):
    count=0
    if isinstance(v,dict):
        text=" ".join(str(v.get(k,"")) for k in ("type","label","kind","category")).lower()
        count+=int(any(x in text for x in labels)); count+=sum(_labels(x,labels) for x in v.values())
    elif isinstance(v,list):count+=sum(_labels(x,labels) for x in v)
    return count

def _size(p):return sum(x.stat().st_size for x in p.rglob("*") if x.is_file()) if p.exists() else 0
