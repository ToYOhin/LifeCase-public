"""Top-level API router."""

from fastapi import APIRouter

from lifecase.api.action_progress import router as action_progress_router
from lifecase.api.actions import router as actions_router
from lifecase.api.agent_runs import router as agent_runs_router
from lifecase.api.analysis_actions import router as analysis_actions_router
from lifecase.api.approvals import router as approvals_router
from lifecase.api.backups import router as backups_router
from lifecase.api.calendar_export import router as calendar_export_router
from lifecase.api.cases import router as cases_router
from lifecase.api.claims import case_router as case_claims_router
from lifecase.api.claims import router as claims_router
from lifecase.api.current_facts import router as current_facts_router
from lifecase.api.deadlines import router as deadlines_router
from lifecase.api.evidence_packs import router as evidence_packs_router
from lifecase.api.live_agent_runs import router as live_agent_runs_router
from lifecase.api.reviews import router as reviews_router
from lifecase.api.sources import router as sources_router
from lifecase.api.system import router as system_router
from lifecase.api.templates import router as templates_router
from lifecase.api.timeline import router as timeline_router
from lifecase.api.warranty_preparation import router as warranty_preparation_router

api_router = APIRouter()
api_router.include_router(system_router, prefix="/system", tags=["system"])
api_router.include_router(templates_router, prefix="/templates", tags=["templates"])
api_router.include_router(actions_router, tags=["actions"])
api_router.include_router(action_progress_router, tags=["actions"])
api_router.include_router(analysis_actions_router, tags=["actions"])
api_router.include_router(agent_runs_router, tags=["agent-runs"])
api_router.include_router(live_agent_runs_router, tags=["live-agent-runs"])
api_router.include_router(approvals_router, tags=["approvals"])
api_router.include_router(backups_router, tags=["backup-restore"])
api_router.include_router(calendar_export_router, tags=["calendar-export"])
api_router.include_router(cases_router, prefix="/cases", tags=["cases"])
api_router.include_router(case_claims_router, tags=["claims"])
api_router.include_router(claims_router, prefix="/claims", tags=["claims"])
api_router.include_router(current_facts_router, tags=["claims"])
api_router.include_router(deadlines_router, tags=["deadlines"])
api_router.include_router(evidence_packs_router, tags=["evidence-packs"])
api_router.include_router(reviews_router, tags=["reviews"])
api_router.include_router(sources_router, tags=["sources"])
api_router.include_router(timeline_router, tags=["timeline"])
api_router.include_router(warranty_preparation_router, tags=["warranty-preparation"])
