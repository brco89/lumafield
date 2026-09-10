import {
  useConversationControls,
  useConversationInput,
  useConversationMode,
  useConversationStatus,
} from "@elevenlabs/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Session = {
  session_id: string;
  session_token: string;
  tool_session_capability: string;
  agent_id: string;
};

type VoiceCredential = {
  voice_credential_type: "CONVERSATION_TOKEN" | "SIGNED_URL";
  voice_credential: string;
};

type Observation = { classification?: string; operator_utterance?: string };
type Registry = {
  classification?: string;
  registered_subtype?: string | null;
  lamp_power_w?: number | null;
  auxiliary_type?: string | null;
  auxiliary_power_w?: number | null;
  registered_power_w?: number | null;
  last_updated_at?: string | null;
  revision?: string;
};
type Identity = {
  address?: string;
  municipality?: string;
  latitude?: number;
  longitude?: number;
  gps_status?: string;
};
type Operational = {
  work_order_id?: string | null;
  work_order_type?: string | null;
  work_order_status?: string | null;
  completed_at?: string | null;
  registry_sync_status?: string;
  queue_status?: string;
  next_step?: string;
};
type Billing = {
  daily_minutes?: number;
  days_in_cycle?: number;
  time_basis?: string;
  label?: string;
  revision?: string;
};
type Assessment = {
  assessment_id?: string;
  evidence_id?: string;
  visual_classification?: string;
  visual_confidence?: string;
  evidence_quality?: string;
  evidence_origin?: string;
  color_temperature_cue?: string;
  identity_consistency?: string;
  visual_cues?: string[];
  limitations?: string[];
  retake_guidance?: string | null;
  observed_power_w?: number | null;
  power_evidence?: string | null;
};
type Decision = {
  disposition?: string;
  final_classification?: string;
  reason_codes?: string[];
  reconciliation_status?: string;
  registered_classification?: string;
  registered_subtype?: string | null;
  registered_power_w?: number | null;
  field_classification?: string;
  observed_power_w?: number | null;
  power_delta_w?: number | null;
  estimated_cycle_energy_delta_kwh?: number | null;
  power_evidence?: string | null;
  technology_status?: string;
  power_status?: string;
  downstream_action?: string;
  intervention_completed_at?: string | null;
  intervention_age_days?: number | null;
  registry_sync_status?: string;
};
type EvidenceReceipt = { evidence_id?: string; inspection_revision?: number };
type ConversationAnalysis = {
  conversation_id: string;
  agent_name?: string | null;
  version_id?: string | null;
  status?: string;
  duration_seconds?: number | null;
  transcript?: Array<{ role?: string; message?: string | null }>;
  analysis?: {
    call_successful?: string;
    transcript_summary?: string;
    evaluation_criteria_results?: Record<string, { result?: string; criteria_id?: string; rationale?: unknown }>;
  };
  received_at?: string;
};
type Inspection = {
  inspection_id: string;
  pole_id?: string | null;
  asset_id?: string | null;
  fixture_id?: string | null;
  identity_status?: string;
  workflow_version?: string;
  revision?: number;
  state?: string;
  identity?: Identity | null;
  registry?: Registry | null;
  operational?: Operational | null;
  billing?: Billing | null;
  original_observation?: Observation | null;
  current_observation?: Observation | null;
  operator_response?: { kind?: string } | null;
  evidence?: EvidenceReceipt[];
  assessments?: Assessment[];
  decision?: Decision | null;
  outcome?: string | null;
  active_operation_id?: string | null;
  remaining_image_attempts?: number;
  recovery_hint?: string;
  allowed_next_actions?: string[];
  message?: string;
  conversation_analysis?: ConversationAnalysis | null;
};
type AssetSummary = {
  fixture_id: string;
  pole_id: string;
  asset_id: string;
  identity: Identity;
  registry: Registry;
  operational: Operational;
};
type InspectionSummary = {
  scope: "ALL_PERSISTED_INSPECTIONS";
  scope_label: string;
  metrics: {
    total_inspections: number;
    unique_poles: number;
    in_progress: number;
    reconciled: number;
    divergent_from_registry: number;
    registry_confirmed: number;
    registry_compatible: number;
    manual_review_required: number;
    unknown_assets: number;
    field_led: number;
    field_legacy: number;
    power_verified: number;
    external_requests_created: number;
    external_requests_pending: number;
    external_requests_unconfirmed: number;
    excluded_superseded_identity_attempts: number;
    verified_power_delta_w: number;
    estimated_cycle_energy_delta_kwh: number;
  };
  suggested_question: string;
  suggested_action: string;
};
type InspectionHistoryItem = {
  inspection_id: string;
  pole_id?: string | null;
  asset_id?: string | null;
  created_at?: string | null;
  state?: string;
  outcome?: string | null;
  revision?: number;
  queue_status?: string;
  next_step?: string;
  identity?: Identity | null;
  registry?: Registry | null;
  operational?: Operational | null;
  field_observation?: Observation | null;
  latest_assessment?: Assessment | null;
  decision?: Decision | null;
  evidence_count: number;
  operation?: {
    status?: string;
    action_type?: string;
    external_reference?: { display_reference?: string; record_id?: string } | null;
  } | null;
};

type Props = { apiBase?: string };
type ConsoleScreen = "operation" | "history";
type UploadPhase = "idle" | "uploading" | "notifying";
type IconName = "brand" | "voice" | "camera" | "eye" | "record" | "check" | "close" | "bolt" | "pin";

const VOICE_CONNECT_TIMEOUT_MS = 20_000;
const PHOTO_ACTIVITY_INTERVAL_MS = 20_000;

const CLASSIFICATION_LABELS: Record<string, string> = {
  LED: "LED",
  LEGACY: "Legacy luminaire",
  INCONCLUSIVE: "Undetermined",
  UNSURE: "Unsure",
};
const SUBTYPE_LABELS: Record<string, string> = {
  HPS: "High-pressure sodium",
  INTEGRATED_LED: "Integrated LED",
  integrated: "Integrated LED",
};
const CONFIDENCE_LABELS: Record<string, string> = {
  HIGH: "high confidence",
  MEDIUM: "medium confidence",
  LOW: "low confidence",
};
const STATE_LABELS: Record<string, string> = {
  AWAITING_OBSERVATION: "Awaiting field observation",
  OPEN: "Verification open",
  NEEDS_EVIDENCE: "Awaiting evidence",
  AWAITING_OPERATOR_RESPONSE: "Awaiting response",
  DECIDED: "Reconciliation ready",
  AWAITING_CONFIRMATION: "Awaiting confirmation",
  ACTION_PENDING: "Request processing",
  ACTION_UNKNOWN: "External result pending",
  COMPLETED: "Verification complete",
};
const QUEUE_LABELS: Record<string, string> = {
  NOT_STARTED: "Not started",
  LIKELY_DIVERGENCE: "Likely discrepancy",
  IN_VERIFICATION: "In verification",
  DIVERGENCE_CONFIRMED: "Discrepancy confirmed",
  REQUEST_PENDING: "Request pending",
  REQUEST_UNCONFIRMED: "Dispatch unconfirmed",
  REQUEST_SENT: "Registry review request sent",
  RESOLVED: "Resolved",
  REVIEW_REQUIRED: "Review required",
  IN_FIELD: "In field",
  FIELD_PENDING: "Inspection pending",
  READY_TO_CONFIRM: "Ready to reconcile",
  OPERATION_EXCEPTION: "Operational exception",
};
const NEXT_STEP_LABELS: Record<string, string> = {
  START_INSPECTION: "Start inspection",
  VERIFY_FIELD: "Verify field",
  CAPTURE_EVIDENCE: "Capture evidence",
  RECONCILE: "Complete reconciliation",
  PREPARE_UPDATE: "Prepare review request",
  CONFIRM_UPDATE: "Confirm review request",
  TRACK_REQUEST: "Track request",
  COMPLETED: "Dossier complete",
  REVIEW: "Route for review",
  RESOLVE: "Resolve discrepancy",
  VERIFY_OPERATING_STATE: "Verify operating state",
};
const STAGES = ["Identify", "Utility Record", "Field", "Evidence", "Reconcile", "Resolve"] as const;
const NEXT_STEP_STAGE: Record<string, number> = {
  START_INSPECTION: 0,
  VERIFY_FIELD: 2,
  CAPTURE_EVIDENCE: 3,
  RECONCILE: 4,
  PREPARE_UPDATE: 5,
  CONFIRM_UPDATE: 5,
  TRACK_REQUEST: 5,
  COMPLETED: 5,
  REVIEW: 5,
  RESOLVE: 5,
  VERIFY_OPERATING_STATE: 2,
};
const QUEUE_TONES: Record<string, string> = {
  NOT_STARTED: "pending",
  LIKELY_DIVERGENCE: "divergent",
  IN_VERIFICATION: "active",
  DIVERGENCE_CONFIRMED: "divergent",
  REQUEST_PENDING: "ready",
  REQUEST_UNCONFIRMED: "warning",
  REQUEST_SENT: "ready",
  RESOLVED: "ready",
  REVIEW_REQUIRED: "warning",
  IN_FIELD: "active",
  FIELD_PENDING: "pending",
  READY_TO_CONFIRM: "ready",
  OPERATION_EXCEPTION: "warning",
};

function Icon({ name, size = 22 }: { name: IconName; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (name === "brand") return <svg {...common}><path d="M3 17 17 3h4v4L7 21H3v-4ZM3 8l5-5h5L3 13V8Z" fill="currentColor" stroke="none" /></svg>;
  if (name === "voice") return <svg {...common}><path d="M4 14v-4M8 18V6M12 21V3M16 17V7M20 14v-4" /></svg>;
  if (name === "camera") return <svg {...common}><path d="M4 7.5h3l1.4-2h7.2l1.4 2h3v11H4v-11Z" /><circle cx="12" cy="13" r="3.5" /></svg>;
  if (name === "eye") return <svg {...common}><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>;
  if (name === "record") return <svg {...common}><path d="M6 3h9l3 3v15H6V3Z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></svg>;
  if (name === "check") return <svg {...common}><path d="m5 12 4 4L19 6" /></svg>;
  if (name === "bolt") return <svg {...common}><path d="m13 2-7 12h6l-1 8 7-12h-6l1-8Z" /></svg>;
  if (name === "pin") return <svg {...common}><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z" /><circle cx="12" cy="10" r="2.5" /></svg>;
  return <svg {...common}><path d="m6 6 12 12M18 6 6 18" /></svg>;
}

async function errorMessage(response: Response, fallback: string) {
  const body = await response.text();
  if (!body) return fallback;
  try {
    return (JSON.parse(body) as { message?: string }).message ?? fallback;
  } catch {
    return response.statusText || fallback;
  }
}

function formatDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

function formatTimestamp(value?: string | null) {
  if (!value) return "Date not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return formatDate(value);
  return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function formatPower(value?: number | null) {
  return value == null ? "Not verified" : `${value.toLocaleString("en-US")} W`;
}

function formatDailyMinutes(value?: number) {
  if (value == null) return "—";
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return `${hours}h${String(minutes).padStart(2, "0")}`;
}

function formatTechnology(classification?: string, subtype?: string | null) {
  if (subtype && SUBTYPE_LABELS[subtype]) return SUBTYPE_LABELS[subtype];
  return CLASSIFICATION_LABELS[classification ?? ""] ?? "Not provided";
}

function formatSigned(value?: number | null, suffix = "W") {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toLocaleString("en-US")} ${suffix}`;
}

function resultCopy(inspection: Inspection | null) {
  const status = inspection?.decision?.reconciliation_status;
  if (status === "DIVERGENT") return ["divergent", "FIELD AND SYSTEM OF RECORD DO NOT MATCH"];
  if (status === "CONFIRMED") return ["confirmed", "Utility record confirmed in field"];
  if (status === "COMPATIBLE") return ["compatible", "Technology is compatible with the utility record"];
  if (status === "INCONCLUSIVE") return ["review", "Reconciliation inconclusive"];
  if (status === "UNKNOWN") return ["review", "Field asset not found in system of record"];
  return null;
}

export function InspectionConsole({ apiBase = "" }: Props) {
  const [screen, setScreen] = useState<ConsoleScreen>("operation");
  const [session, setSession] = useState<Session | null>(null);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [assets, setAssets] = useState<AssetSummary[]>([]);
  const [assetQueueStatus, setAssetQueueStatus] = useState<"loading" | "ready" | "error">("loading");
  const [summary, setSummary] = useState<InspectionSummary | null>(null);
  const [summaryStatus, setSummaryStatus] = useState<"loading" | "ready" | "error">("loading");
  const [history, setHistory] = useState<InspectionHistoryItem[]>([]);
  const [historyStatus, setHistoryStatus] = useState<"loading" | "ready" | "error">("loading");
  const [selectedInspectionId, setSelectedInspectionId] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("Ready to begin verification.");
  const [problem, setProblem] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadPhase, setUploadPhase] = useState<UploadPhase>("idle");
  const previewRef = useRef<string | null>(null);
  const inspectionRef = useRef<string | null>(null);
  const sessionRequestIdRef = useRef(crypto.randomUUID());
  const notifiedEvidenceRef = useRef(new Set<string>());
  const notifiedRecoveryRef = useRef(new Set<string>());
  const {
    startSession,
    endSession,
    sendContextualUpdate,
    sendUserMessage,
    sendUserActivity,
  } = useConversationControls();
  const { isMuted, setMuted } = useConversationInput();
  const { status } = useConversationStatus();
  const { isSpeaking } = useConversationMode();

  const toggleMicrophone = useCallback(() => {
    const nextMuted = !isMuted;
    setMuted(nextMuted);
    setAnnouncement(
      nextMuted
        ? "Microphone muted. The voice session and workflow remain active."
        : "Microphone resumed. The agent can hear you again.",
    );
  }, [isMuted, setMuted]);

  const stopVoiceSession = useCallback(() => {
    if (isMuted) setMuted(false);
    endSession();
  }, [endSession, isMuted, setMuted]);

  const createConsoleSession = useCallback(async () => {
    const response = await fetch(`${apiBase}/v1/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_id: sessionRequestIdRef.current,
        operator_display_name: "Inspector",
      }),
    });
    if (!response.ok) {
      const detail = await errorMessage(response, "Failed to create the session.");
      throw new Error(detail);
    }
    return await response.json() as Session;
  }, [apiBase]);

  useEffect(() => {
    let stopped = false;
    setAssetQueueStatus("loading");
    void createConsoleSession()
      .then((next) => {
        if (!stopped) setSession(next);
      })
      .catch((error) => {
        if (stopped) return;
        const detail = String(error instanceof Error ? error.message : error);
        setAssetQueueStatus("error");
        setProblem(detail);
        setAnnouncement(`The field queue could not be loaded: ${detail}`);
      });
    return () => { stopped = true; };
  }, [createConsoleSession]);

  useEffect(() => {
    if (!session) return;
    let stopped = false;
    const refresh = async () => {
      try {
        const headers = { Authorization: `Bearer ${session.session_token}` };
        const [sessionResponse, assetsResponse, summaryResponse, historyResponse] = await Promise.all([
          fetch(`${apiBase}/v1/sessions/${session.session_id}`, { headers }),
          fetch(`${apiBase}/v1/assets`, { headers }),
          fetch(`${apiBase}/v1/analytics/inspections`, { headers }),
          fetch(`${apiBase}/v1/inspections`, { headers }),
        ]);
        if (!stopped && sessionResponse.ok) {
          const state = await sessionResponse.json();
          setInspection(state.active_inspection ?? null);
        }
        if (!stopped && assetsResponse.ok) {
          const list = await assetsResponse.json();
          setAssets(list.assets ?? []);
          setAssetQueueStatus("ready");
        } else if (!stopped) {
          setAssetQueueStatus("error");
        }
        if (!stopped && summaryResponse.ok) {
          setSummary(await summaryResponse.json() as InspectionSummary);
          setSummaryStatus("ready");
        } else if (!stopped) {
          setSummaryStatus("error");
        }
        if (!stopped && historyResponse.ok) {
          const result = await historyResponse.json() as { inspections?: InspectionHistoryItem[] };
          const nextHistory = result.inspections ?? [];
          setHistory(nextHistory);
          setSelectedInspectionId((current) => current && nextHistory.some((item) => item.inspection_id === current)
            ? current
            : nextHistory[0]?.inspection_id ?? null);
          setHistoryStatus("ready");
        } else if (!stopped) {
          setHistoryStatus("error");
        }
      } catch {
        if (!stopped) {
          setAssetQueueStatus("error");
          setSummaryStatus("error");
          setHistoryStatus("error");
        }
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [apiBase, session]);

  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
  }, []);

  useEffect(() => {
    const nextId = inspection?.inspection_id ?? null;
    if (inspectionRef.current && nextId && inspectionRef.current !== nextId) {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
      previewRef.current = null;
      setPreviewUrl(null);
      setUploadPhase("idle");
    }
    inspectionRef.current = nextId;
  }, [inspection?.inspection_id]);

  useEffect(() => {
    if (inspection?.inspection_id) setScreen("operation");
  }, [inspection?.inspection_id]);

  const allowedActions = inspection?.allowed_next_actions ?? [];
  const waitingForPhoto = allowedActions.includes("UPLOAD_EVIDENCE");

  useEffect(() => {
    if (status !== "connected" || !waitingForPhoto) return;
    const keepQuiet = () => {
      try { sendUserActivity(); } catch { /* A later pulse or upload event can recover this. */ }
    };
    keepQuiet();
    const timer = window.setInterval(keepQuiet, PHOTO_ACTIVITY_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [sendUserActivity, status, waitingForPhoto]);

  useEffect(() => {
    if (status !== "connected" || !allowedActions.includes("ASSESS_EVIDENCE")) return;
    const assessed = new Set(
      (inspection?.assessments ?? []).map((assessment) => assessment.evidence_id).filter(Boolean),
    );
    const pending = [...(inspection?.evidence ?? [])].reverse().find(
      (receipt) => receipt.evidence_id && !assessed.has(receipt.evidence_id),
    );
    const evidenceId = pending?.evidence_id;
    if (!inspection || !evidenceId || notifiedEvidenceRef.current.has(evidenceId)) return;
    notifiedEvidenceRef.current.add(evidenceId);
    try {
      sendContextualUpdate(JSON.stringify({
        source: "lumafield_client",
        event: "EVIDENCE_UPLOADED",
        inspection_id: inspection.inspection_id,
        evidence_id: evidenceId,
        expected_revision: pending.inspection_revision ?? inspection.revision,
      }), { contextId: `lumafield-evidence-${evidenceId}` });
      // The structured contextual update carries authority and identifiers.
      // This neutral turn only wakes the conversation after the upload. An
      // imperative pseudo-system message is correctly rejected by ElevenLabs'
      // prompt-injection guardrail.
      sendUserMessage("Photo attached.");
      setUploadPhase("idle");
      setAnnouncement("Evidence received. Assessment started automatically.");
    } catch (error) {
      notifiedEvidenceRef.current.delete(evidenceId);
      setProblem(`The photo was saved, but the agent has not received the notification. ${String(error instanceof Error ? error.message : error)}`);
    }
  }, [allowedActions, inspection, sendContextualUpdate, sendUserMessage, status]);

  useEffect(() => {
    if (status !== "connected" || !inspection
        || inspection.recovery_hint !== "RECONCILE_EXHAUSTED_EVIDENCE"
        || notifiedRecoveryRef.current.has(inspection.inspection_id)) return;
    notifiedRecoveryRef.current.add(inspection.inspection_id);
    try {
      sendContextualUpdate(JSON.stringify({
        source: "lumafield_client",
        event: "EVIDENCE_ATTEMPTS_EXHAUSTED",
        inspection_id: inspection.inspection_id,
        expected_revision: inspection.revision,
        allowed_next_action: "RECONCILE",
      }), { contextId: `lumafield-recovery-${inspection.inspection_id}` });
      sendUserMessage("Evidence capture is complete.");
      setAnnouncement("Evidence limit reached. Preparing review.");
    } catch (error) {
      notifiedRecoveryRef.current.delete(inspection.inspection_id);
      setProblem(`Review is available, but the agent has not received the notification. ${String(error instanceof Error ? error.message : error)}`);
    }
  }, [inspection, sendContextualUpdate, sendUserMessage, status]);

  const begin = useCallback(async () => {
    const needsFreshSession = Boolean(conversationId);
    setBusy(true);
    setScreen("operation");
    setProblem(null);
    setConversationId(null);
    setInspection(null);
    notifiedEvidenceRef.current.clear();
    notifiedRecoveryRef.current.clear();
    let connectionStarted = false;
    try {
      setAnnouncement("Opening secure session…");
      let next = session;
      if (needsFreshSession || !next) {
        if (needsFreshSession) sessionRequestIdRef.current = crypto.randomUUID();
        next = await createConsoleSession();
        setSession(next);
      }
      const credentialResponse = await fetch(`${apiBase}/v1/sessions/${next.session_id}/voice-credential`, {
        method: "POST",
        headers: { Authorization: `Bearer ${next.session_token}` },
      });
      if (!credentialResponse.ok) {
        throw new Error(await errorMessage(credentialResponse, "Failed to open the voice session."));
      }
      const voice = await credentialResponse.json() as VoiceCredential;
      const credential = voice.voice_credential_type === "SIGNED_URL"
        ? { signedUrl: voice.voice_credential }
        : { conversationToken: voice.voice_credential };
      let settle: (outcome: { id?: string; failure?: string }) => void = () => {};
      const connected = new Promise<{ id?: string; failure?: string }>((resolve) => { settle = resolve; });
      const timeout = window.setTimeout(
        () => settle({ failure: "The voice connection did not respond. Check the microphone and try again." }),
        VOICE_CONNECT_TIMEOUT_MS,
      );
      connectionStarted = true;
      startSession({
        ...credential,
        dynamicVariables: { "secret__lumafield_session": next.tool_session_capability },
        onConnect: ({ conversationId }) => settle({ id: conversationId }),
        onError: (detail) => settle({ failure: String(detail) }),
      } as Parameters<typeof startSession>[0]);
      const outcome = await connected.finally(() => window.clearTimeout(timeout));
      if (outcome.failure) throw new Error(outcome.failure);
      if (!outcome.id) throw new Error("The conversation did not report a Conversation ID.");
      setConversationId(outcome.id);
      const bound = await fetch(`${apiBase}/v1/sessions/${next.session_id}/bind`, {
        method: "POST",
        headers: { Authorization: `Bearer ${next.session_token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: outcome.id }),
      });
      if (!bound.ok) throw new Error(await errorMessage(bound, "Failed to bind the conversation."));
      setAnnouncement("Connected. Ask a question or start a new inspection.");
    } catch (error) {
      if (connectionStarted) {
        try { await endSession(); } catch { /* Keep the original error. */ }
      }
      setConversationId(null);
      setInspection(null);
      const detail = String(error instanceof Error ? error.message : error);
      setProblem(detail);
      setAnnouncement(`The session could not be opened: ${detail}`);
    } finally {
      setBusy(false);
    }
  }, [apiBase, conversationId, createConsoleSession, endSession, session, startSession]);

  const upload = useCallback(async (file: File) => {
    if (!session || !inspection) return;
    setProblem(null);
    setUploadPhase("uploading");
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    const url = URL.createObjectURL(file);
    previewRef.current = url;
    setPreviewUrl(url);
    setAnnouncement("Uploading evidence…");
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(
        `${apiBase}/v1/inspections/${inspection.inspection_id}/evidence?expected_revision=${inspection.revision}`,
        { method: "POST", headers: { Authorization: `Bearer ${session.session_token}` }, body },
      );
      const result = await response.json();
      if (!response.ok) throw new Error(result.message ?? "The photo could not be uploaded.");
      setInspection((current) => {
        if (!current || current.inspection_id !== inspection.inspection_id) return current;
        const evidence = [...(current.evidence ?? [])];
        if (!evidence.some((receipt) => receipt.evidence_id === result.evidence_id)) evidence.push(result);
        return {
          ...current,
          revision: result.inspection_revision,
          evidence,
          allowed_next_actions: result.duplicate ? current.allowed_next_actions : ["ASSESS_EVIDENCE"],
        };
      });
      setUploadPhase("notifying");
      setAnnouncement("Evidence received. Notifying the agent…");
    } catch (error) {
      const detail = String(error instanceof Error ? error.message : error);
      setProblem(detail);
      setUploadPhase("idle");
      setAnnouncement(detail);
    }
  }, [apiBase, inspection, session]);

  const connected = status !== "disconnected";
  const assessments = inspection?.assessments ?? [];
  const latestAssessment = assessments.at(-1);
  const technologyAssessment = [...assessments].reverse().find((item) => item.visual_classification && item.visual_classification !== "INCONCLUSIVE");
  const powerAssessment = [...assessments].reverse().find((item) => item.observed_power_w != null);
  const evidenceCount = inspection?.evidence?.length ?? 0;
  const isAnalyzing = uploadPhase !== "idle" || allowedActions.includes("ASSESS_EVIDENCE");
  const canUpload = connected && waitingForPhoto && uploadPhase === "idle";
  const result = resultCopy(inspection);
  const registry = inspection?.registry;
  const operation = inspection?.operational;
  const decision = inspection?.decision;
  const currentObservation = inspection?.current_observation ?? inspection?.original_observation;
  const observationWasCorrected = Boolean(
    inspection?.original_observation?.classification
    && currentObservation?.classification
    && inspection.original_observation.classification !== currentObservation.classification,
  );
  const fieldClass = decision?.field_classification ?? technologyAssessment?.visual_classification ?? currentObservation?.classification;
  const observedPower = decision?.observed_power_w ?? powerAssessment?.observed_power_w;
  const registeredPower = decision?.registered_power_w ?? registry?.registered_power_w;
  const technologyMismatch = Boolean(registry?.classification && fieldClass && fieldClass !== "UNSURE" && fieldClass !== "INCONCLUSIVE" && registry.classification !== fieldClass);
  const needsLabelPhoto = waitingForPhoto && technologyMismatch && observedPower == null && assessments.length > 0;
  const postCall = inspection?.conversation_analysis;
  const evaluationEntries = Object.entries(postCall?.analysis?.evaluation_criteria_results ?? {});

  const currentStage = useMemo(() => {
    if (!inspection) return 0;
    if (inspection.identity_status !== "FOUND") return 0;
    if (inspection.state === "AWAITING_OBSERVATION") return 2;
    if (!inspection.original_observation) return 2;
    if (!evidenceCount) return 3;
    if (!decision) return allowedActions.includes("RECONCILE") ? 4 : 3;
    if (inspection.state === "AWAITING_CONFIRMATION" || inspection.state === "ACTION_PENDING" || inspection.state === "COMPLETED") return 5;
    return 4;
  }, [allowedActions, decision, evidenceCount, inspection]);
  const workspaceMode = decision || currentStage >= 4 ? "resolution" : waitingForPhoto || isAnalyzing ? "evidence" : "field";
  const comparisonSymbol = decision?.reconciliation_status === "DIVERGENT"
    ? "≠"
    : decision?.reconciliation_status === "CONFIRMED" || decision?.reconciliation_status === "COMPATIBLE"
      ? "="
      : "↔";
  const comparisonLabel = comparisonSymbol === "≠"
    ? "Utility record and evidence do not match"
    : comparisonSymbol === "="
      ? "Utility record and evidence match"
      : "Utility record and evidence under comparison";
  const registeredTechnology = `${formatTechnology(registry?.classification, registry?.registered_subtype)}${registry?.auxiliary_power_w ? " + ballast" : ""}`;
  const fieldTechnology = formatTechnology(fieldClass);

  const instruction = useMemo(() => {
    if (!connected) return ["idle", "Start with voice", "Start the agent to ask about history or begin an inspection."];
    if (!inspection) return ["live", "Ask a question or start an inspection", "Ask about history or say “Start a new inspection.”"];
    if (inspection.identity_status !== "FOUND") return ["live", "Check the pole number", "This number was not found in the utility record. Repeat the digits to correct it."];
    if (inspection.state === "AWAITING_OBSERVATION") return ["live", "Compare the record with the street", "Say whether the installed luminaire appears to be LED, legacy, or uncertain."];
    if (uploadPhase === "uploading") return ["working", "Saving the photo", "Assessment will begin next."];
    if (isAnalyzing) return ["working", "Assessing the evidence", "Please wait a moment."];
    if (waitingForPhoto) return [
      "waiting",
      needsLabelPhoto ? "Photograph the label, if safely visible" : assessments.length ? "Another piece of evidence is needed" : "Photograph the entire luminaire",
      needsLabelPhoto
        ? "Technology already differs. Power will be recorded only if it is legible on the label or model."
        : latestAssessment?.retake_guidance || inspection.message || "Take your time. The agent will wait silently.",
    ];
    if (allowedActions.includes("RECONCILE")) return ["working", "Comparing utility record and field", "Please wait a moment."];
    if (inspection.state === "AWAITING_CONFIRMATION") return ["decision", "One operational confirmation", "Confirm only if you want to send the discrepancy or action to the external system."];
    if (inspection.state === "COMPLETED") return ["success", "Verification complete", "The result is recorded in the pole dossier."];
    if (decision) return [result?.[0] ?? "decision", "Reconciliation ready", "Review the result and next step."];
    return ["live", "Verification in progress", "Follow the agent’s next instruction."];
  }, [allowedActions, assessments.length, connected, decision, inspection, isAnalyzing, latestAssessment, needsLabelPhoto, result, uploadPhase, waitingForPhoto]);
  const selectedHistory = useMemo(
    () => history.find((item) => item.inspection_id === selectedInspectionId) ?? history[0] ?? null,
    [history, selectedInspectionId],
  );
  const historyDecision = selectedHistory?.decision;
  const historyFieldClass = historyDecision?.field_classification
    ?? selectedHistory?.latest_assessment?.visual_classification
    ?? selectedHistory?.field_observation?.classification;
  const showEvidenceStage = currentStage >= 3;

  return (
    <main className="field-console" data-state={inspection?.state ?? "IDLE"} data-workspace-mode={workspaceMode}>
      <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
      <header className="field-header">
        <a className="brand" href="#main-workspace" aria-label="LumaField — go to verification">
          <Icon name="brand" size={28} /><span>LumaField</span><small>Urban Operations</small>
        </a>
        <nav className="screen-switcher" aria-label="LumaField sections">
          <button type="button" aria-current={screen === "operation" ? "page" : undefined} onClick={() => setScreen("operation")}>New Inspection</button>
          <button type="button" aria-current={screen === "history" ? "page" : undefined} onClick={() => setScreen("history")}>Inspections</button>
        </nav>
        <div className="header-asset"><span>Current Pole</span><strong>{inspection?.pole_id ?? "Not identified"}</strong></div>
        <div className="voice-cluster">
          <div className="voice-readout" data-status={status} data-muted={isMuted || undefined}>
            <span className={`voice-wave ${isSpeaking ? "is-speaking" : ""}`}><Icon name="voice" /></span>
            <span><strong>{status === "connected" ? (isMuted ? "Mic Muted" : isSpeaking ? "Agent Speaking" : "Agent Connected") : status === "connecting" ? "Connecting" : "Voice Off"}</strong><small>{isMuted ? "Session remains active" : "ElevenLabs voice agent"}</small></span>
          </div>
          {!connected ? (
            <button className="session-button" onClick={() => void begin()} disabled={busy}>{busy ? "Starting…" : "Start Agent"}</button>
          ) : (
            <div className="voice-actions">
              <button
                type="button"
                className="session-button session-button--quiet session-button--mute"
                aria-pressed={isMuted}
                onClick={toggleMicrophone}
              >{isMuted ? "Resume Mic" : "Mute Mic"}</button>
              <button type="button" className="session-button session-button--quiet" onClick={stopVoiceSession}>End Session</button>
            </div>
          )}
        </div>
      </header>

      {problem && <div className="problem-banner" role="alert"><Icon name="close" size={18} /><span>{problem}</span><button onClick={() => setProblem(null)}>Close</button></div>}

      <section className="history-screen" id={screen === "history" ? "main-workspace" : undefined} hidden={screen !== "history"} aria-labelledby="inspection-summary-title">
        <header className="history-heading">
          <div><h1 id="inspection-summary-title">Inspections</h1><p>Portfolio view and dossiers for every recorded verification.</p></div>
          <div className="voice-shortcuts" aria-label="Example question for the agent">
            <div className="voice-shortcuts-title"><Icon name="voice" size={18} /><span>Ask the Agent</span></div>
            <p><strong>Question</strong><q>{summary?.suggested_question ?? "How many inspections found a discrepancy with the utility record?"}</q></p>
          </div>
        </header>
        <dl className="history-metrics" aria-label="Metrics for all recorded inspections">
          <div><dt>Inspections</dt><dd>{summaryStatus === "ready" ? summary?.metrics.total_inspections ?? 0 : "—"}</dd><small>{summary?.scope_label ?? "Syncing history"}</small></div>
          <div data-tone="divergent"><dt>Discrepant</dt><dd>{summaryStatus === "ready" ? summary?.metrics.divergent_from_registry ?? 0 : "—"}</dd><small>Inspections with discrepancies</small></div>
          <div data-tone="confirmed"><dt>Confirmed</dt><dd>{summaryStatus === "ready" ? summary?.metrics.registry_confirmed ?? 0 : "—"}</dd><small>Utility record confirmed in field</small></div>
          <div data-tone="review"><dt>In Review</dt><dd>{summaryStatus === "ready" ? summary?.metrics.manual_review_required ?? 0 : "—"}</dd><small>Require human analysis</small></div>
          <div><dt>Verified Delta</dt><dd>{summaryStatus === "ready" ? formatSigned(summary?.metrics.verified_power_delta_w ?? 0) : "—"}</dd><small>Sum of verified load differences</small></div>
        </dl>
        <div className="history-workspace">
          <section className="history-index" aria-labelledby="history-index-title">
            <div className="history-index-heading"><h2 id="history-index-title">Operational History</h2><span>{historyStatus === "ready" ? `${history.length} records` : historyStatus === "error" ? "Unavailable" : "Loading"}</span></div>
            {historyStatus === "ready" && history.length > 0 ? <ol>{history.map((item) => <li key={item.inspection_id}>
              <button type="button" aria-pressed={item.inspection_id === selectedHistory?.inspection_id} onClick={() => setSelectedInspectionId(item.inspection_id)}>
                <span className="history-row-main"><strong>Pole {item.pole_id ?? "not identified"}</strong><span>{QUEUE_LABELS[item.queue_status ?? ""] ?? STATE_LABELS[item.state ?? ""] ?? "In verification"}</span></span>
                <span className="history-row-meta"><span>{formatTimestamp(item.created_at)}</span><span>{item.evidence_count} {item.evidence_count === 1 ? "evidence item" : "evidence items"}</span></span>
              </button>
            </li>)}</ol> : <div className="history-empty"><strong>{historyStatus === "error" ? "Inspections could not be loaded" : "No inspections recorded"}</strong><p>{historyStatus === "error" ? "The application will retry automatically." : "Dossiers will appear here after the first pole is identified."}</p></div>}
          </section>
          <aside className="history-dossier" aria-label="Selected inspection details">
            {selectedHistory ? <>
              <header><div><span>Inspection Dossier</span><h2>Pole {selectedHistory.pole_id ?? "not identified"}</h2><p>{selectedHistory.identity?.address ?? "Location not recorded"}</p></div><div className="history-status"><strong>{QUEUE_LABELS[selectedHistory.queue_status ?? ""] ?? "In verification"}</strong><span>{formatTimestamp(selectedHistory.created_at)}</span></div></header>
              <div className="history-truth">
                <section><h3><Icon name="record" size={18} /> Utility Record</h3><strong>{selectedHistory.registry ? formatTechnology(selectedHistory.registry.classification, selectedHistory.registry.registered_subtype) : "Unavailable"}</strong><dl><div><dt>Registered Load</dt><dd>{formatPower(selectedHistory.registry?.registered_power_w)}</dd></div><div><dt>Last Updated</dt><dd>{formatDate(selectedHistory.registry?.last_updated_at)}</dd></div></dl></section>
                <span className="history-comparison" aria-hidden>{historyDecision?.reconciliation_status === "DIVERGENT" ? "≠" : historyDecision ? "=" : "↔"}</span>
                <section><h3><Icon name="eye" size={18} /> Field</h3><strong>{formatTechnology(historyFieldClass)}</strong><dl><div><dt>Verified Load</dt><dd>{formatPower(historyDecision?.observed_power_w ?? selectedHistory.latest_assessment?.observed_power_w)}</dd></div><div><dt>Evidence Items</dt><dd>{selectedHistory.evidence_count}</dd></div></dl></section>
              </div>
              <section className="history-outcome" data-tone={QUEUE_TONES[selectedHistory.queue_status ?? ""] ?? "pending"}>
                <div><span>Result</span><h3>{historyDecision ? resultCopy({ inspection_id: selectedHistory.inspection_id, decision: historyDecision } as Inspection)?.[1] ?? "Reconciliation recorded" : STATE_LABELS[selectedHistory.state ?? ""] ?? "Verification in progress"}</h3></div>
                <div><span>Impact</span><strong>{formatSigned(historyDecision?.power_delta_w)}</strong><small>{historyDecision?.estimated_cycle_energy_delta_kwh != null ? `${formatSigned(historyDecision.estimated_cycle_energy_delta_kwh, "kWh")} / cycle` : "No verified energy delta"}</small></div>
              </section>
              <dl className="history-audit"><div><dt>Inspection</dt><dd><code>{selectedHistory.inspection_id}</code></dd></div><div><dt>State</dt><dd>{STATE_LABELS[selectedHistory.state ?? ""] ?? selectedHistory.state ?? "—"}</dd></div><div><dt>Revision</dt><dd>{selectedHistory.revision ?? "—"}</dd></div><div><dt>External Reference</dt><dd>{selectedHistory.operation?.external_reference?.display_reference ?? "—"}</dd></div></dl>
            </> : <div className="history-empty"><strong>Select an inspection</strong><p>The utility record, evidence, and result will appear in this dossier.</p></div>}
          </aside>
        </div>
        <p className="sr-only" role="status" aria-live="polite">{summaryStatus === "ready" ? `Summary updated: ${summary?.metrics.total_inspections ?? 0} recorded inspections; ${summary?.metrics.divergent_from_registry ?? 0} with a utility-record discrepancy.` : ""}</p>
      </section>

      <div className="operation-screen" hidden={screen !== "operation"}>
      {screen === "operation" && inspection && <nav className="workflow-strip" aria-label="Reconciliation progress">
        <ol className="route-line">
          {STAGES.map((label, index) => {
            const complete = inspection.state === "COMPLETED" || index < currentStage;
            const active = connected && index === currentStage && inspection.state !== "COMPLETED";
            return <li key={label} className={complete ? "is-complete" : active ? "is-active" : ""} aria-current={active ? "step" : undefined}>
              <span className="route-node">{complete ? <Icon name="check" size={14} /> : index + 1}</span>
              <span><strong>{label}</strong><span className="sr-only">{complete ? "Complete" : active ? "Current stage" : "Pending"}</span></span>
            </li>;
          })}
        </ol>
      </nav>}

      <section className={`reconciliation-shell ${inspection ? "" : "is-idle"}`} hidden={screen !== "operation"}>
        <aside className="asset-queue" aria-label="Asset queue">
          <div className="queue-heading"><strong>Field Queue</strong><span>{assetQueueStatus === "ready" ? `${assets.length} points` : assetQueueStatus === "error" ? "Unavailable" : "Loading"}</span></div>
          <ol>
            {assetQueueStatus !== "ready" ? (
              <li className="queue-system-state"><span className="queue-copy"><strong>{assetQueueStatus === "error" ? "Queue unavailable" : "Loading queue"}</strong><small>{assetQueueStatus === "error" ? "The system cannot be queried right now." : "Retrieving operational state…"}</small></span></li>
            ) : assets.map((asset) => {
              const active = asset.pole_id === inspection?.pole_id;
              const stage = active ? currentStage : NEXT_STEP_STAGE[asset.operational.next_step ?? ""] ?? 0;
              const tone = QUEUE_TONES[asset.operational.queue_status ?? ""] ?? "pending";
              return <li key={asset.pole_id} className={active ? "is-current" : ""} data-tone={tone} aria-current={active ? "true" : undefined}>
                <span className="queue-id"><span className="queue-pole">{asset.pole_id}</span>{active && <span className="queue-focus">In focus</span>}</span>
                <span className="queue-copy"><strong>{QUEUE_LABELS[asset.operational.queue_status ?? ""] ?? "To verify"}</strong><small>{active ? STAGES[stage] : NEXT_STEP_LABELS[asset.operational.next_step ?? ""] ?? "Open dossier"} · {stage + 1}/{STAGES.length}</small></span>
              </li>;
            })}
          </ol>
        </aside>

        {!inspection ? <section className="inspection-launch" id="main-workspace" aria-labelledby="inspection-launch-title">
          <div className="launch-signal"><span className={`voice-wave ${isSpeaking ? "is-speaking" : ""}`}><Icon name="voice" size={28} /></span><span>{connected ? "Agent ready" : "Field agent"}</span></div>
          <h1 id="inspection-launch-title">Ready for the next pole</h1>
          <p>The queue stays visible. The dossier, camera, and reconciliation appear only when the workflow needs them.</p>
          <div className="launch-command"><span>Voice action</span><q>Start a new inspection</q></div>
          {!connected ? <button className="session-button" type="button" onClick={() => void begin()} disabled={busy}>{busy ? "Starting…" : "Start Agent"}</button> : <div className="launch-listening"><span aria-hidden />Say the action when you are ready</div>}
        </section> : <section className="workbench" id="main-workspace" data-mode={workspaceMode} data-evidence-visible={showEvidenceStage}>
          {showEvidenceStage && <article className="evidence-stage" aria-label="Visual evidence for the asset">
            <div className="evidence-canvas">
              {previewUrl ? <img src={previewUrl} alt={`Photo submitted for pole ${inspection?.pole_id ?? "current"}`} /> : (
                <div className="evidence-empty">
                  <span className="camera-frame"><Icon name="camera" size={42} /></span>
                  <h1>{waitingForPhoto ? (needsLabelPhoto ? "Frame the label" : "Frame the luminaire") : inspection ? "Asset evidence" : "The street is the physical source"}</h1>
                  <p>{waitingForPhoto
                    ? needsLabelPhoto
                      ? "Show power, model, and manufacturer only if the label is visible from a safe position."
                      : "Show the entire luminaire. Classification is only one piece of reconciliation evidence."
                    : inspection
                      ? "The next evidence item appears here when the workflow reaches this stage."
                      : "Identify the pole by voice to compare utility record, field, and operational history."}</p>
                </div>
              )}
              <div className="evidence-meta"><span>{previewUrl ? (needsLabelPhoto ? "Label / Model" : "Current Evidence") : "Evidence Custody"}</span>{evidenceCount > 0 && <span>{evidenceCount} {evidenceCount === 1 ? "image" : "images"}</span>}</div>
              {isAnalyzing && <div className="analysis-sweep" aria-hidden><span /></div>}
            </div>
            <label className={`capture-action ${canUpload ? "" : "is-disabled"}`}>
              <input type="file" accept="image/jpeg,image/png" capture="environment" disabled={!canUpload} onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(file);
                event.target.value = "";
              }} />
              <Icon name="camera" size={28} />
              <span><strong>{uploadPhase === "uploading" ? "Uploading evidence…" : waitingForPhoto ? (needsLabelPhoto ? "Photograph label or model" : "Photograph luminaire") : "Capture unavailable at this stage"}</strong><small>{waitingForPhoto ? "JPG or PNG · up to 10 MB · take your time" : inspection ? "Continue through the conversation" : "Available after identifying the pole"}</small></span>
            </label>
          </article>}

          <aside className="operation-lane" aria-label="Pole dossier and reconciliation">
            <div className="operation-mast">
              <section className={`pole-identity ${inspection?.pole_id ? "" : "is-empty"}`}>
                <span>Work Unit</span>
                {inspection?.pole_id ? <strong>{inspection.pole_id}</strong> : <strong>Awaiting pole</strong>}
                <small>{inspection?.identity?.address ?? (connected ? "Say only the number" : "Verification not started")}{inspection?.identity?.municipality && <><br />{inspection.identity.municipality}</>}</small>
              </section>
              <section className="agent-instruction" data-tone={instruction[0]}>
                <div className="agent-presence"><span className={`voice-wave ${isSpeaking ? "is-speaking" : ""}`}><Icon name="voice" /></span><span>{waitingForPhoto ? "Agent waiting silently" : isSpeaking ? "Agent speaking" : "Reconciliation agent"}</span></div>
                <h2>{instruction[1]}</h2><p>{instruction[2]}</p>
                {isAnalyzing && <div className="working-line" aria-label="Assessment in progress"><span /></div>}
              </section>
            </div>

            {result && decision && <section className="reconciliation-result" data-result={result[0]}>
              <div className="result-copy">
                <span>Reconciliation Result</span>
                <h2>{result[1]}</h2>
                <p>{registeredTechnology} <span aria-hidden>→</span><span className="sr-only">to</span> {fieldTechnology}</p>
              </div>
              {(registeredPower != null || observedPower != null || decision.power_delta_w != null) && <div className="result-metrics">
                <div className="load-change"><span>{formatPower(registeredPower)}</span><span aria-hidden>→</span><span className="sr-only">to</span><span>{formatPower(observedPower)}</span></div>
                {decision.power_delta_w != null && <strong>{formatSigned(decision.power_delta_w)}</strong>}
                {decision.estimated_cycle_energy_delta_kwh != null && <small>{formatSigned(decision.estimated_cycle_energy_delta_kwh, "kWh")} / cycle</small>}
              </div>}
            </section>}

            <section className="truth-board" aria-label="Reconciliation sources">
              <div className="truth-source truth-source--registry">
                <h3><Icon name="record" size={19} /> Utility Record</h3>
                {operation?.work_order_status === "COMPLETED" && <div className="registry-context">
                  <strong>Work order {operation.work_order_id} completed · {formatDate(operation.completed_at)}</strong>
                  <span data-sync={operation.registry_sync_status}>{operation.registry_sync_status === "PENDING" ? "Registry Sync Pending" : "Registry Synchronized"}</span>
                </div>}
                {registry ? <>
                  <strong>{formatTechnology(registry.classification, registry.registered_subtype)}</strong>
                  <dl>
                    <div><dt>Lamp</dt><dd>{formatPower(registry.lamp_power_w)}</dd></div>
                    <div><dt>{registry.auxiliary_type === "BALLAST" ? "Ballast" : "Auxiliary"}</dt><dd>{formatPower(registry.auxiliary_power_w)}</dd></div>
                    <div className="load-total"><dt>Registered Load</dt><dd>{formatPower(registry.registered_power_w)}</dd></div>
                  </dl>
                  <p>Last Updated {formatDate(registry.last_updated_at)}</p>
                </> : <p>The utility record appears as soon as the agent identifies the pole.</p>}
              </div>

              <div className="truth-divider" aria-label={comparisonLabel}><span aria-hidden>{comparisonSymbol}</span></div>

              <div className="truth-source truth-source--field">
                <h3><Icon name="eye" size={19} /> Field Evidence</h3>
                {inspection?.original_observation ? <>
                  <strong>{formatTechnology(fieldClass)}</strong>
                  <dl>
                    <div><dt>Technician Observation</dt><dd>{CLASSIFICATION_LABELS[currentObservation?.classification ?? ""] ?? "Recorded"}{observationWasCorrected ? ` · corrected from ${CLASSIFICATION_LABELS[inspection.original_observation.classification ?? ""] ?? inspection.original_observation.classification}` : ""}</dd></div>
                    <div><dt>General Photo</dt><dd>{technologyAssessment ? `${formatTechnology(technologyAssessment.visual_classification)} · ${CONFIDENCE_LABELS[technologyAssessment.visual_confidence ?? ""] ?? "confidence not reported"}` : "Awaiting photo"}</dd></div>
                    <div><dt>Label / Model</dt><dd>{powerAssessment?.observed_power_w != null ? formatPower(powerAssessment.observed_power_w) : "Not provided"}</dd></div>
                    <div className="load-total"><dt>Verified Load</dt><dd>{formatPower(observedPower)}</dd></div>
                  </dl>
                  <p>{powerAssessment?.power_evidence ?? (observedPower == null ? "Power was not inferred from appearance." : "Power is legible in the evidence.")}</p>
                </> : <p>The field observation remains separate from the utility record and visual assessment.</p>}
              </div>
            </section>

            {operation && operation.work_order_status !== "COMPLETED" && <section className="operational-context">
              <div><span className="row-icon"><Icon name="bolt" /></span><div><h3>Operational Context</h3><strong>{operation.work_order_status === "COMPLETED" ? "Modernization complete" : operation.work_order_id ? "Open work order" : "No linked intervention"}</strong><p>{operation.completed_at ? `Work order ${operation.work_order_id} · completed ${formatDate(operation.completed_at)}` : operation.work_order_id ?? "No work order provided"}</p></div></div>
              <div className={operation.registry_sync_status === "PENDING" ? "sync-alert" : ""}><span>{operation.registry_sync_status === "PENDING" ? "Utility record not yet updated" : "Registry synchronized"}</span>{decision?.intervention_age_days != null && <small>{decision.intervention_age_days} days since intervention</small>}</div>
            </section>}

            {decision && <section className={`resolution-row ${inspection?.outcome ? "is-complete" : ""}`}>
              <span className="row-icon"><Icon name={inspection?.outcome ? "check" : "pin"} /></span>
              <div><h3>Resolve</h3>{decision ? <><strong>{decision.downstream_action === "REGISTRY_REVIEW" ? "Registry Review" : decision.downstream_action === "REPLACEMENT" ? "Contract-Based Replacement" : result?.[0] === "confirmed" || result?.[0] === "compatible" ? "No registry correction required" : "Human Review"}</strong><p>{inspection?.state === "AWAITING_CONFIRMATION" ? "Awaiting confirmation before external dispatch." : inspection?.outcome ? "Result recorded in the asset dossier." : "The action appears only after reconciliation."}</p></> : <p>Operational consequences follow the comparison between the utility record and field evidence.</p>}</div>
            </section>}

            <details className="audit-drawer"><summary>Provenance, Conversation, and Audit</summary><dl>
              <div><dt>Conversation</dt><dd><code>{conversationId ?? "—"}</code></dd></div>
              <div><dt>Asset</dt><dd><code>{inspection?.asset_id ?? "—"}</code></dd></div>
              <div><dt>Inspection</dt><dd><code>{inspection?.inspection_id ?? "—"}</code></dd></div>
              <div><dt>State</dt><dd>{STATE_LABELS[inspection?.state ?? ""] ?? (connected ? "Awaiting pole" : "Not started")}</dd></div>
              <div><dt>Revision</dt><dd>{inspection?.revision ?? "—"}</dd></div>
              <div><dt>Evidence Items</dt><dd>{evidenceCount}</dd></div>
              <div><dt>Coordinates</dt><dd>{inspection?.identity?.latitude != null && inspection?.identity?.longitude != null ? `${inspection.identity.latitude}, ${inspection.identity.longitude}` : "—"}</dd></div>
              {inspection?.billing && <div><dt>Time Basis</dt><dd>{inspection.billing.label} · {formatDailyMinutes(inspection.billing.daily_minutes)}/day · {inspection.billing.days_in_cycle} days</dd></div>}
              {inspection?.active_operation_id && <div><dt>Operation</dt><dd><code>{inspection.active_operation_id}</code></dd></div>}
              <div><dt>Post-Call</dt><dd>{postCall ? `${postCall.analysis?.call_successful === "success" ? "Evaluation complete" : "Analysis received"}${postCall.duration_seconds != null ? ` · ${postCall.duration_seconds}s` : ""}` : "Awaiting conversation end"}</dd></div>
              {postCall?.version_id && <div><dt>Agent Version</dt><dd><code>{postCall.version_id}</code></dd></div>}
              {postCall?.transcript && <div><dt>Transcript</dt><dd>{postCall.transcript.length} turns preserved</dd></div>}
              {evaluationEntries.length > 0 && <div className="audit-evaluations"><dt>Success Evals</dt><dd>{evaluationEntries.filter(([, value]) => value.result === "success").length}/{evaluationEntries.length} criteria passed</dd></div>}
              {postCall?.analysis?.transcript_summary && <div className="audit-evaluations"><dt>Summary</dt><dd>{postCall.analysis.transcript_summary}</dd></div>}
            </dl>{assessments.length > 1 && <details className="attempt-history"><summary>View Earlier Evidence</summary><ol>{assessments.slice(0, -1).map((assessment, index) => <li key={assessment.assessment_id ?? index}>{formatTechnology(assessment.visual_classification)}{assessment.observed_power_w != null ? ` · ${formatPower(assessment.observed_power_w)}` : ""}</li>)}</ol></details>}</details>
          </aside>
        </section>}
      </section>
      </div>
    </main>
  );
}
