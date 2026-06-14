import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Plus } from "lucide-react";

import { AdminHero, AdminPage, AdminSurface } from "@/components/admin-shell";
import { fetchInternalRoadmap, saveInternalRoadmap } from "@/lib/admin-api";

import "./roadmap.css";

type ItemStatus = "todo" | "inprogress" | "inreview" | "done";
type ItemPriority = "high" | "medium" | "low";
type ItemEffort = "S" | "M" | "L";
type ItemLabel = "bug" | "improvement" | "enhancement" | "feature" | "performance" | "ux" | "data" | "infrastructure";

type RoadmapFilters = {
  status: "all" | ItemStatus;
  priority: "all" | ItemPriority;
  effort: "all" | ItemEffort;
  label: "all" | ItemLabel;
};

const DEFAULT_FILTERS: RoadmapFilters = {
  status: "all",
  priority: "all",
  effort: "all",
  label: "all",
};

type RoadmapItem = {
  id: string;
  title: string;
  status: ItemStatus;
  priority: ItemPriority;
  effort: ItemEffort;
  notes: string;
  labels?: string[];
};

type RoadmapPhase = {
  id: string;
  title: string;
  period: string;
  items: RoadmapItem[];
};

const DEFAULT_PHASES: RoadmapPhase[] = [
  {
    id: "phase1",
    title: "Phase 1 — Beta Launch",
    period: "May–June 2026",
    items: [
      { id: "p1-1", labels: [], title: "Public beta launch on TWF", status: "done", priority: "high", effort: "L", notes: "Main post + two cross-posts in active threads. Live." },
      { id: "p1-2", labels: ["bug"], title: "Satellite animation stall bug fix", status: "done", priority: "high", effort: "S", notes: "Caught via session replay on day one. Fixed and deployed." },
      { id: "p1-3", labels: ["enhancement"], title: "Animation play button loops back from last frame", status: "done", priority: "medium", effort: "S", notes: "Previously hitting play at end of scrubber did nothing. Now loops to start." },
      { id: "p1-4", labels: ["enhancement"], title: "Animation loops until explicit stop", status: "done", priority: "medium", effort: "S", notes: "Changed from stop-at-end to continuous loop on user stop." },
      { id: "p1-5", labels: ["bug","performance"], title: "Fix scrubber lag before cache warm", status: "todo", priority: "high", effort: "M", notes: "Increase prefetch aggressiveness so more frames are cached before user starts scrubbing. GitHub issue filed." },
      { id: "p1-6", labels: ["bug"], title: "Fix value tooltip desync with map coloring", status: "todo", priority: "high", effort: "M", notes: "Tooltip shows value for different forecast hour than currently rendered. GitHub issue filed." },
      { id: "p1-7", labels: ["bug"], title: "Fix frame freeze on early scrub", status: "todo", priority: "high", effort: "M", notes: "Map freezes on old frame if user scrubs too early. Does not update until scrubbed again. GitHub issue filed. Race condition suspected." },
      { id: "p1-8", labels: ["bug","ux"], title: "Fix city label alignment on mobile", status: "todo", priority: "medium", effort: "S", notes: "Labels not aligning to map dots on mobile when zoomed in. Likely pixel density / anchor offset issue on high-DPI screens. Reported by TWF beta user." },
      { id: "p1-9", labels: ["enhancement"], title: "Preserve forecast hour when switching models/products", status: "todo", priority: "medium", effort: "M", notes: "Snap to same or nearest available hour when switching. Clamp to max if out of range. Handle models with different variable/hour availability gracefully. GitHub issue filed." },
    ],
  },
  {
    id: "phase2",
    title: "Phase 2 — Core Expansion",
    period: "June–July 2026",
    items: [
      { id: "p2-1", labels: ["enhancement"], title: "NWS warnings overlay on radar", status: "todo", priority: "high", effort: "S", notes: "Half to one day lift on existing infrastructure. Highest impact Phase 2 item for TWF audience — radar feels incomplete without warnings." },
      { id: "p2-2", labels: ["ux","enhancement"], title: "Animation speed control", status: "todo", priority: "high", effort: "S", notes: "Slow/Normal/Fast toggle. AUTOPLAY_TICK_MS already exists as config value. Requested by multiple beta users and noted from session replay." },
      { id: "p2-3", labels: ["enhancement"], title: "Expand satellite to additional bands", status: "todo", priority: "medium", effort: "M", notes: "Band 13 live for v1. Expand to additional bands. Low-risk addition." },
      { id: "p2-4", labels: ["data"], title: "Expand SPC outlooks to long range", status: "todo", priority: "medium", effort: "M", notes: "" },
      { id: "p2-5", labels: ["data"], title: "Expand CPC outlooks to long range", status: "todo", priority: "medium", effort: "M", notes: "" },
      { id: "p2-6", labels: ["data"], title: "Add new secondary models", status: "todo", priority: "medium", effort: "M", notes: "Let beta feedback inform which models matter most to TWF audience before committing." },
      { id: "p2-7", labels: ["data"], title: "Climate indices page (SSTs, MJO, PNA, NAO, AO)", status: "todo", priority: "medium", effort: "S", notes: "Start with embed approach using official NOAA/CPC charts. One to two days. Opportunistic — slot in between bigger features." },
      { id: "p2-8", labels: ["ux"], title: "Improve share modal hierarchy", status: "todo", priority: "medium", effort: "S", notes: "Copy link should be prominent for all users. TWF sharing is secondary for linked accounts. Screenshot download equally visible. Currently buried behind TWF sign-in CTA." },
    ],
  },
  {
    id: "phase3",
    title: "Phase 3 — Power Features",
    period: "July–September 2026",
    items: [
      { id: "p3-1", labels: ["enhancement"], title: "Meteograms", status: "todo", priority: "high", effort: "L", notes: "Highest demand Phase 3 feature. Backend data largely in place. Model similar to WB meteograms. Explicit user demand from TWF thread. Give it real time — half-baked meteograms will disappoint." },
      { id: "p3-2", labels: ["enhancement"], title: "GIF export in share modal", status: "todo", priority: "high", effort: "L", notes: "Three-tab share modal: Image / GIF / Link. GIF is lighter lift than comparison tools." },
      { id: "p3-3", labels: ["enhancement"], title: "Side-by-side model comparison tool", status: "todo", priority: "high", effort: "L", notes: "Synchronized dual MapLibre instances. GPU shader difference mode. Most architecturally complex Phase 3 item — needs real runway." },
      { id: "p3-4", labels: ["enhancement"], title: "Run-to-run deltas", status: "todo", priority: "high", effort: "L", notes: "Shares infrastructure with comparison tool. Requested by TWF beta user." },
      { id: "p3-5", labels: ["data"], title: "Model consensus and probabilities", status: "todo", priority: "medium", effort: "L", notes: "" },
      { id: "p3-6", labels: ["data","enhancement"], title: "Integrate climatology baseline into forecast page", status: "todo", priority: "medium", effort: "M", notes: "ERA5 infrastructure already built and powering anomaly maps. Primarily a UI integration task." },
      { id: "p3-7", labels: ["enhancement"], title: "Skew-T diagrams", status: "todo", priority: "medium", effort: "L", notes: "Mentioned in TWF beta feedback. High value for technical TWF crowd. Do correctly — high-stakes for credibility." },
      { id: "p3-8", labels: ["enhancement"], title: "Add meteograms to forecast page", status: "todo", priority: "medium", effort: "M", notes: "" },
      { id: "p3-9", labels: ["data"], title: "Spaghetti plots and ensemble spread charts", status: "todo", priority: "low", effort: "L", notes: "Requested by TWF beta user referencing existing community content. Good longer-term addition." },
    ],
  },
  {
    id: "phase4",
    title: "Phase 4 — Pre-Busy Season Polish",
    period: "September–October 2026",
    items: [
      { id: "p4-1", labels: ["infrastructure"], title: "Feature freeze and stability pass", status: "todo", priority: "high", effort: "M", notes: "No new features after late September. Focus on performance and reliability before traffic spike." },
      { id: "p4-2", labels: ["performance","infrastructure"], title: "Performance audit and optimization", status: "todo", priority: "high", effort: "M", notes: "Ensure viewer handles concurrent users well. Review API worker / scheduler memory profile under load." },
      { id: "p4-3", labels: ["data"], title: "Additional models (post-beta feedback informed)", status: "todo", priority: "medium", effort: "M", notes: "Save new model additions for last so data pipeline management does not overlap complex UI feature work." },
      { id: "p4-4", labels: ["ux"], title: "Mobile responsive improvements", status: "todo", priority: "medium", effort: "M", notes: "PWA explicitly decided against. Responsive design fixes are the correct investment given mobile-majority TWF audience." },
    ],
  },
  {
    id: "phase5",
    title: "Phase 5 — Busy Season & Beyond",
    period: "October 2026+",
    items: [
      { id: "p5-1", labels: ["enhancement"], title: "Rollout monetization (Pro tier)", status: "todo", priority: "high", effort: "L", notes: "Groundwork already laid — Stripe billing lifecycle validated, Clerk publicMetadata plan gating in place. Execute post-busy-season ramp." },
      { id: "p5-2", labels: ["enhancement"], title: "Expand social sharing targets (Discord, X, Facebook)", status: "todo", priority: "medium", effort: "M", notes: "Discord requested by TWF beta user (PDX weather group). X and Facebook identified as initial targets. YouTube content creator use case also noted." },
      { id: "p5-3", labels: ["enhancement"], title: "Storm/event mode", status: "todo", priority: "medium", effort: "L", notes: "" },
      { id: "p5-4", labels: ["enhancement"], title: "NWS warning polygons", status: "todo", priority: "medium", effort: "S", notes: "Half-day lift on existing infrastructure." },
      { id: "p5-5", labels: ["data"], title: "Lightning strike data (GOES GLM)", status: "todo", priority: "medium", effort: "M", notes: "GOES GLM recommended. 2–3 days estimated." },
      { id: "p5-6", labels: ["enhancement"], title: "Pressure center H/L labels", status: "todo", priority: "low", effort: "S", notes: "" },
      { id: "p5-7", labels: ["ux"], title: "Location favorites on forecast page", status: "todo", priority: "low", effort: "S", notes: "" },
      { id: "p5-8", labels: ["ux"], title: "Client-side screenshot revisit", status: "todo", priority: "low", effort: "M", notes: "Originally client-side but viewport consistency caused screenshot to differ from displayed view — trust issue. Revisit if viewport normalization can be solved without mismatching output." },
      { id: "p5-9", labels: ["infrastructure","performance"], title: "Remove val.cog and sample off grid binaries", status: "todo", priority: "low", effort: "L", notes: "Possibly replace val.cog with point sampling directly off grid binaries." },
      { id: "p5-10", labels: ["infrastructure"], title: "Server upgrade evaluation", status: "todo", priority: "low", effort: "S", notes: "Netcup RS 8000 G12 at 82€/mo vs Hetzner AX42 at ~49€/mo. Pending ECMWF memory investigation outcome." },
    ],
  },
];

const STATUS_ORDER: ItemStatus[] = ["todo", "inprogress", "inreview", "done"];
const PRIORITY_ORDER: ItemPriority[] = ["high", "medium", "low"];
const EFFORT_ORDER: ItemEffort[] = ["S", "M", "L"];

const BUGS_IMPROVEMENTS_LABELS: ItemLabel[] = ["bug", "improvement"];

const ITEM_LABELS: ItemLabel[] = ["bug", "improvement", "enhancement", "feature", "performance", "ux", "data", "infrastructure"];

const LABEL_STYLES: Record<ItemLabel, { color: string; bg: string; border: string }> = {
  bug: { color: "#f85149", bg: "rgba(248, 81, 73, 0.15)", border: "rgba(248, 81, 73, 0.35)" },
  improvement: { color: "#e3b341", bg: "rgba(227, 179, 65, 0.15)", border: "rgba(227, 179, 65, 0.35)" },
  enhancement: { color: "#58a6ff", bg: "rgba(88, 166, 255, 0.15)", border: "rgba(88, 166, 255, 0.35)" },
  feature: { color: "#3fb950", bg: "rgba(63, 185, 80, 0.15)", border: "rgba(63, 185, 80, 0.35)" },
  performance: { color: "#f59e0b", bg: "rgba(245, 158, 11, 0.15)", border: "rgba(245, 158, 11, 0.35)" },
  ux: { color: "#a371f7", bg: "rgba(163, 113, 247, 0.15)", border: "rgba(163, 113, 247, 0.35)" },
  data: { color: "#2dd4bf", bg: "rgba(45, 212, 191, 0.15)", border: "rgba(45, 212, 191, 0.35)" },
  infrastructure: { color: "#8b949e", bg: "rgba(139, 148, 158, 0.15)", border: "rgba(139, 148, 158, 0.35)" },
};

function hasActiveFilters(filters: RoadmapFilters): boolean {
  return (
    filters.status !== "all"
    || filters.priority !== "all"
    || filters.effort !== "all"
    || filters.label !== "all"
  );
}

function clonePhases(phases: RoadmapPhase[]): RoadmapPhase[] {
  return normalizePhases(structuredClone(phases));
}

function normalizePhases(phases: RoadmapPhase[]): RoadmapPhase[] {
  return phases.map((phase) => ({
    ...phase,
    items: phase.items.map((item) => ({
      ...item,
      labels: item.labels ?? [],
    })),
  }));
}

function itemLabels(item: RoadmapItem): string[] {
  return item.labels ?? [];
}

function isItemLabel(value: string): value is ItemLabel {
  return ITEM_LABELS.includes(value as ItemLabel);
}

function isBugsImprovementsItem(item: RoadmapItem): boolean {
  const labels = itemLabels(item);
  return labels.some((label) => BUGS_IMPROVEMENTS_LABELS.includes(label as ItemLabel));
}

function uid(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function statusLabel(status: ItemStatus): string {
  return { todo: "To Do", inprogress: "In Progress", inreview: "In Review", done: "Done" }[status];
}

function priorityLabel(priority: ItemPriority): string {
  return priority.charAt(0).toUpperCase() + priority.slice(1);
}

function effortLabel(effort: ItemEffort): string {
  return { S: "Small", M: "Medium", L: "Large" }[effort];
}

function itemMatchesFilters(item: RoadmapItem, filters: RoadmapFilters): boolean {
  if (filters.status !== "all" && item.status !== filters.status) return false;
  if (filters.priority !== "all" && item.priority !== filters.priority) return false;
  if (filters.effort !== "all" && item.effort !== filters.effort) return false;
  if (filters.label !== "all" && !itemLabels(item).includes(filters.label)) return false;
  return true;
}

function isRoadmapPhases(value: unknown): value is RoadmapPhase[] {
  return Array.isArray(value) && value.length > 0;
}

function phaseShortTitle(title: string): string {
  return title.split("—")[0].trim();
}

export default function AdminRoadmapPage() {
  const [phases, setPhases] = useState<RoadmapPhase[]>(() => clonePhases(DEFAULT_PHASES));
  const [filters, setFilters] = useState<RoadmapFilters>(DEFAULT_FILTERS);
  const [toastMessage, setToastMessage] = useState("");
  const [toastVisible, setToastVisible] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [modalTitle, setModalTitle] = useState("");
  const [modalPhaseId, setModalPhaseId] = useState(DEFAULT_PHASES[0].id);
  const [modalStatus, setModalStatus] = useState<ItemStatus>("todo");
  const [modalPriority, setModalPriority] = useState<ItemPriority>("medium");
  const [modalEffort, setModalEffort] = useState<ItemEffort>("M");
  const [modalNotes, setModalNotes] = useState("");
  const [modalLabels, setModalLabels] = useState<ItemLabel[]>([]);
  const [bugsSectionOpen, setBugsSectionOpen] = useState(true);
  const [expandedDoneSections, setExpandedDoneSections] = useState<Set<string>>(() => new Set());
  const [loading, setLoading] = useState(true);

  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string) => {
    setToastMessage(message);
    setToastVisible(true);
    if (toastTimerRef.current) {
      clearTimeout(toastTimerRef.current);
    }
    toastTimerRef.current = setTimeout(() => setToastVisible(false), 2000);
  }, []);

  const save = useCallback(async (nextPhases: RoadmapPhase[]) => {
    try {
      await saveInternalRoadmap(nextPhases);
      showToast("Saved");
    } catch {
      showToast("Save failed");
    }
  }, [showToast]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchInternalRoadmap();
        if (!cancelled && isRoadmapPhases(data)) {
          setPhases(normalizePhases(data));
        }
      } catch {
        if (!cancelled) {
          setPhases(clonePhases(DEFAULT_PHASES));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!modalOpen) return;

    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setModalOpen(false);
        setEditingId(null);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [modalOpen]);

  const progress = useMemo(() => {
    let total = 0;
    let done = 0;
    phases.forEach((phase) => {
      phase.items.forEach((item) => {
        total += 1;
        if (item.status === "done") done += 1;
      });
    });
    const pct = total ? Math.round((done / total) * 100) : 0;
    return { total, done, pct };
  }, [phases]);

  const bugsImprovementsItems = useMemo(() => {
    const entries: Array<{ item: RoadmapItem; phaseId: string }> = [];
    phases.forEach((phase) => {
      phase.items.forEach((item) => {
        if (isBugsImprovementsItem(item)) {
          entries.push({ item, phaseId: phase.id });
        }
      });
    });
    return entries;
  }, [phases]);

  const bugsOpenCount = useMemo(
    () => bugsImprovementsItems.filter(({ item }) => item.status !== "done").length,
    [bugsImprovementsItems],
  );

  const visibleBugsItems = useMemo(
    () => bugsImprovementsItems.filter(({ item }) => itemMatchesFilters(item, filters)),
    [bugsImprovementsItems, filters],
  );

  function toggleDoneSection(sectionKey: string) {
    setExpandedDoneSections((current) => {
      const next = new Set(current);
      if (next.has(sectionKey)) {
        next.delete(sectionKey);
      } else {
        next.add(sectionKey);
      }
      return next;
    });
  }

  function updatePhases(updater: (current: RoadmapPhase[]) => RoadmapPhase[]) {
    setPhases((current) => {
      const next = updater(current);
      void save(next);
      return next;
    });
  }

  function findItem(id: string): RoadmapItem | null {
    for (const phase of phases) {
      const item = phase.items.find((entry) => entry.id === id);
      if (item) return item;
    }
    return null;
  }

  function openAddModal() {
    setEditingId(null);
    setModalTitle("");
    setModalStatus("todo");
    setModalPriority("medium");
    setModalEffort("M");
    setModalNotes("");
    setModalLabels([]);
    setModalPhaseId(phases[0]?.id ?? DEFAULT_PHASES[0].id);
    setModalOpen(true);
  }

  function openEditModal(id: string) {
    const item = findItem(id);
    if (!item) return;
    const phase = phases.find((entry) => entry.items.some((candidate) => candidate.id === id));
    setEditingId(id);
    setModalTitle(item.title);
    setModalStatus(item.status);
    setModalPriority(item.priority);
    setModalEffort(item.effort);
    setModalNotes(item.notes || "");
    setModalLabels(itemLabels(item).filter(isItemLabel));
    setModalPhaseId(phase?.id ?? phases[0]?.id ?? DEFAULT_PHASES[0].id);
    setModalOpen(true);
  }

  function closeModal() {
    setModalOpen(false);
    setEditingId(null);
  }

  function saveModal() {
    const title = modalTitle.trim();
    if (!title) {
      showToast("Title required");
      return;
    }

    updatePhases((current) => {
      const next = clonePhases(current);
      if (editingId) {
        const fromPhase = next.find((phase) => phase.items.some((item) => item.id === editingId));
        const toPhase = next.find((phase) => phase.id === modalPhaseId);
        if (!fromPhase || !toPhase) return current;

        const item = fromPhase.items.find((entry) => entry.id === editingId);
        if (!item) return current;

        if (fromPhase.id !== toPhase.id) {
          fromPhase.items = fromPhase.items.filter((entry) => entry.id !== editingId);
          Object.assign(item, {
            title,
            status: modalStatus,
            priority: modalPriority,
            effort: modalEffort,
            notes: modalNotes.trim(),
            labels: [...modalLabels],
          });
          toPhase.items.push(item);
        } else {
          Object.assign(item, {
            title,
            status: modalStatus,
            priority: modalPriority,
            effort: modalEffort,
            notes: modalNotes.trim(),
            labels: [...modalLabels],
          });
        }
      } else {
        const phase = next.find((entry) => entry.id === modalPhaseId);
        if (!phase) return current;
        phase.items.push({
          id: uid(),
          title,
          status: modalStatus,
          priority: modalPriority,
          effort: modalEffort,
          notes: modalNotes.trim(),
          labels: [...modalLabels],
        });
      }
      return next;
    });

    closeModal();
  }

  function saveTitle(id: string, value: string) {
    const trimmed = value.trim();
    updatePhases((current) => {
      const next = clonePhases(current);
      const item = next.flatMap((phase) => phase.items).find((entry) => entry.id === id);
      if (!item || !trimmed) return current;
      item.title = trimmed;
      return next;
    });
  }

  function saveNotes(id: string, value: string) {
    const trimmed = value.trim();
    updatePhases((current) => {
      const next = clonePhases(current);
      const item = next.flatMap((phase) => phase.items).find((entry) => entry.id === id);
      if (!item) return current;
      item.notes = trimmed;
      return next;
    });
  }

  function toggleDone(id: string) {
    updatePhases((current) => {
      const next = clonePhases(current);
      const item = next.flatMap((phase) => phase.items).find((entry) => entry.id === id);
      if (!item) return current;
      item.status = item.status === "done" ? "todo" : "done";
      return next;
    });
  }

  function cycleStatus(id: string) {
    updatePhases((current) => {
      const next = clonePhases(current);
      const item = next.flatMap((phase) => phase.items).find((entry) => entry.id === id);
      if (!item) return current;
      const index = STATUS_ORDER.indexOf(item.status);
      item.status = STATUS_ORDER[(index + 1) % STATUS_ORDER.length];
      return next;
    });
  }

  function cyclePriority(id: string) {
    updatePhases((current) => {
      const next = clonePhases(current);
      const item = next.flatMap((phase) => phase.items).find((entry) => entry.id === id);
      if (!item) return current;
      const index = PRIORITY_ORDER.indexOf(item.priority);
      item.priority = PRIORITY_ORDER[(index + 1) % PRIORITY_ORDER.length];
      return next;
    });
  }

  function cycleEffort(id: string) {
    updatePhases((current) => {
      const next = clonePhases(current);
      const item = next.flatMap((phase) => phase.items).find((entry) => entry.id === id);
      if (!item) return current;
      const index = EFFORT_ORDER.indexOf(item.effort);
      item.effort = EFFORT_ORDER[(index + 1) % EFFORT_ORDER.length];
      return next;
    });
  }

  function deleteItem(id: string, phaseId: string) {
    updatePhases((current) => {
      const next = clonePhases(current);
      const phase = next.find((entry) => entry.id === phaseId);
      if (!phase) return current;
      phase.items = phase.items.filter((item) => item.id !== id);
      return next;
    });
  }

  if (loading) {
    return (
      <div className="relative min-h-[calc(100vh-3.5rem)] w-full bg-[#07111f] text-white">
        <div className="mx-auto max-w-[1100px] px-4 py-24 pt-[4.5rem] text-sm text-white/60 md:px-5">
          Loading roadmap…
        </div>
      </div>
    );
  }

  return (
    <div className="roadmap-page relative min-h-[calc(100vh-3.5rem)] w-full overflow-x-hidden text-white">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: `
            radial-gradient(1200px 720px at 15% 10%, rgba(72,160,220,0.14), transparent 56%),
            radial-gradient(900px 620px at 82% 18%, rgba(52,211,203,0.08), transparent 58%),
            linear-gradient(to bottom, rgba(6,12,24,0.82), rgba(6,12,24,0.96))
          `,
        }}
      />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(7,17,31,0.2),rgba(7,17,31,0.62))]" />

      <div className="relative mx-auto max-w-[1100px] px-4 pb-28 pt-[4.5rem] md:px-5 md:pb-32">
        <AdminPage>
          <AdminHero eyebrow="Internal" title="Roadmap" />

          <AdminSurface
            title="Filters & progress"
            headerRight={
              hasActiveFilters(filters) ? (
                <button
                  type="button"
                  onClick={() => setFilters(DEFAULT_FILTERS)}
                  className="text-xs font-semibold uppercase tracking-[0.14em] text-cyan-200/72 transition hover:text-cyan-100"
                >
                  Clear filters
                </button>
              ) : null
            }
          >
            <div className="flex flex-wrap items-end gap-3 md:gap-4">
              <FilterDropdown
                label="Status"
                value={filters.status}
                options={[
                  { value: "all", label: "All statuses" },
                  { value: "todo", label: "To Do" },
                  { value: "inprogress", label: "In Progress" },
                  { value: "inreview", label: "In Review" },
                  { value: "done", label: "Done" },
                ]}
                onChange={(value) => setFilters((current) => ({ ...current, status: value as RoadmapFilters["status"] }))}
              />
              <FilterDropdown
                label="Priority"
                value={filters.priority}
                options={[
                  { value: "all", label: "All priorities" },
                  { value: "high", label: "High" },
                  { value: "medium", label: "Medium" },
                  { value: "low", label: "Low" },
                ]}
                onChange={(value) => setFilters((current) => ({ ...current, priority: value as RoadmapFilters["priority"] }))}
              />
              <FilterDropdown
                label="Effort"
                value={filters.effort}
                options={[
                  { value: "all", label: "All effort" },
                  { value: "S", label: "Small" },
                  { value: "M", label: "Medium" },
                  { value: "L", label: "Large" },
                ]}
                onChange={(value) => setFilters((current) => ({ ...current, effort: value as RoadmapFilters["effort"] }))}
              />
              <FilterDropdown
                label="Labels"
                value={filters.label}
                options={[
                  { value: "all", label: "All labels" },
                  ...ITEM_LABELS.map((label) => ({ value: label, label })),
                ]}
                onChange={(value) => setFilters((current) => ({ ...current, label: value as RoadmapFilters["label"] }))}
              />
            </div>

            <div className="mt-5 flex items-center gap-4 border-t border-white/8 pt-5">
              <span className="text-xs font-mono uppercase tracking-[0.12em] text-white/45">Overall progress</span>
              <div className="progress-track flex-1">
                <div className="progress-fill" style={{ width: `${progress.pct}%` }} />
              </div>
              <span className="text-xs font-mono text-cyan-200/80">
                {progress.pct}% · {progress.done} / {progress.total} done
              </span>
            </div>
          </AdminSurface>

        {bugsImprovementsItems.length > 0 && (
          <AdminSurface
            className="bugs-section-surface"
            title="Bugs & Improvements"
            headerRight={
              <button
                type="button"
                onClick={() => setBugsSectionOpen((open) => !open)}
                className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-orange-300/80 transition hover:text-orange-200"
              >
                {bugsOpenCount} open
                <span aria-hidden="true">{bugsSectionOpen ? "▾" : "▸"}</span>
              </button>
            }
          >
            {bugsSectionOpen ? (
              visibleBugsItems.length === 0 ? (
                <div className="bugs-section-empty">No items match the current filters.</div>
              ) : (
                <RoadmapSectionItems
                  sectionKey="bugs"
                  entries={visibleBugsItems}
                  expandedDoneSections={expandedDoneSections}
                  onToggleDoneSection={toggleDoneSection}
                  onToggleDone={toggleDone}
                  onSaveTitle={saveTitle}
                  onSaveNotes={saveNotes}
                  onCycleStatus={cycleStatus}
                  onCyclePriority={cyclePriority}
                  onCycleEffort={cycleEffort}
                  onEdit={openEditModal}
                  onDelete={deleteItem}
                />
              )
            ) : (
              <div className="text-sm text-white/45">Section collapsed.</div>
            )}
          </AdminSurface>
        )}

        {phases.map((phase) => {
          const phaseItems = phase.items.filter((item) => !isBugsImprovementsItem(item));
          const visibleItems = phaseItems.filter((item) => itemMatchesFilters(item, filters));
          if (hasActiveFilters(filters) && visibleItems.length === 0) return null;

          const doneCount = phaseItems.filter((item) => item.status === "done").length;
          const itemsToShow = hasActiveFilters(filters) ? visibleItems : phaseItems;

          return (
            <AdminSurface
              key={phase.id}
              title={phase.title}
              description={
                <span className="font-mono text-xs text-white/40">{phase.period}</span>
              }
              headerRight={
                <span className="font-mono text-xs text-white/35">{doneCount}/{phaseItems.length}</span>
              }
            >
              <RoadmapSectionItems
                sectionKey={phase.id}
                entries={itemsToShow.map((item) => ({ item, phaseId: phase.id }))}
                expandedDoneSections={expandedDoneSections}
                onToggleDoneSection={toggleDoneSection}
                onToggleDone={toggleDone}
                onSaveTitle={saveTitle}
                onSaveNotes={saveNotes}
                onCycleStatus={cycleStatus}
                onCyclePriority={cyclePriority}
                onCycleEffort={cycleEffort}
                onEdit={openEditModal}
                onDelete={deleteItem}
              />
            </AdminSurface>
          );
        })}
        </AdminPage>
      </div>

      <button
        type="button"
        onClick={openAddModal}
        className="fixed bottom-6 right-6 z-40 flex h-12 items-center gap-2 rounded-full border border-cyan-200/40 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 text-sm font-semibold text-slate-950 shadow-[0_14px_40px_rgba(35,196,255,0.28)] transition hover:brightness-105 md:bottom-8 md:right-8"
      >
        <Plus className="h-4 w-4" />
        Add Item
      </button>

      {modalOpen && (
        <div
          className="modal-overlay"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeModal();
          }}
        >
          <div className="modal">
            <h3>{editingId ? "Edit Item" : "Add Item"}</h3>
            <div className="modal-field">
              <label className="modal-label">Title</label>
              <input
                className="modal-input"
                value={modalTitle}
                onChange={(event) => setModalTitle(event.target.value)}
                placeholder="What needs to be done?"
              />
            </div>
            <div className="modal-field">
              <label className="modal-label">Phase</label>
              <select
                className="modal-select"
                value={modalPhaseId}
                onChange={(event) => setModalPhaseId(event.target.value)}
              >
                {phases.map((phase) => (
                  <option key={phase.id} value={phase.id}>{phaseShortTitle(phase.title)}</option>
                ))}
              </select>
            </div>
            <div className="modal-field" style={{ display: "flex", gap: "10px" }}>
              <div style={{ flex: 1 }}>
                <label className="modal-label">Status</label>
                <select
                  className="modal-select"
                  value={modalStatus}
                  onChange={(event) => setModalStatus(event.target.value as ItemStatus)}
                >
                  <option value="todo">To Do</option>
                  <option value="inprogress">In Progress</option>
                  <option value="inreview">In Review</option>
                  <option value="done">Done</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label className="modal-label">Priority</label>
                <select
                  className="modal-select"
                  value={modalPriority}
                  onChange={(event) => setModalPriority(event.target.value as ItemPriority)}
                >
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label className="modal-label">Effort</label>
                <select
                  className="modal-select"
                  value={modalEffort}
                  onChange={(event) => setModalEffort(event.target.value as ItemEffort)}
                >
                  <option value="S">Small</option>
                  <option value="M">Medium</option>
                  <option value="L">Large</option>
                </select>
              </div>
            </div>
            <div className="modal-field">
              <label className="modal-label">Labels</label>
              <div className="modal-label-pills">
                {ITEM_LABELS.map((label) => {
                  const selected = modalLabels.includes(label);
                  const style = LABEL_STYLES[label];
                  return (
                    <button
                      key={label}
                      type="button"
                      className={`label-pill-toggle${selected ? " selected" : ""}`}
                      style={{
                        color: style.color,
                        background: selected ? style.bg : "transparent",
                        borderColor: style.border,
                      }}
                      onClick={() => {
                        setModalLabels((current) =>
                          current.includes(label)
                            ? current.filter((entry) => entry !== label)
                            : [...current, label],
                        );
                      }}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="modal-field">
              <label className="modal-label">Notes</label>
              <textarea
                className="modal-textarea"
                value={modalNotes}
                onChange={(event) => setModalNotes(event.target.value)}
                placeholder="Optional notes, links, context..."
              />
            </div>
            <div className="modal-actions">
              <button type="button" className="btn btn-ghost" onClick={closeModal}>Cancel</button>
              <button type="button" className="btn btn-primary" onClick={saveModal}>Save</button>
            </div>
          </div>
        </div>
      )}

      <div className={`toast${toastVisible ? " show" : ""}`}>{toastMessage}</div>
    </div>
  );
}

function FilterDropdown(props: {
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div className="filter-dropdown">
      <label className="filter-dropdown-label">{props.label}</label>
      <select
        className="filter-dropdown-select"
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
      >
        {props.options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </div>
  );
}

function MetaBadgeGroup(props: {
  label: string;
  badgeClassName: string;
  badgeText: string;
  onClick: () => void;
}) {
  return (
    <span className="meta-inline-group">
      <span className="meta-inline-label">{props.label}:</span>
      <span className={props.badgeClassName} onClick={props.onClick}>
        {props.badgeText}
      </span>
    </span>
  );
}

function LabelPill(props: { label: ItemLabel }) {
  const style = LABEL_STYLES[props.label];
  return (
    <span
      className="label-pill"
      style={{
        color: style.color,
        background: style.bg,
        borderColor: style.border,
      }}
    >
      {props.label}
    </span>
  );
}

type RoadmapItemEntry = { item: RoadmapItem; phaseId: string };

function RoadmapSectionItems(props: {
  sectionKey: string;
  entries: RoadmapItemEntry[];
  expandedDoneSections: Set<string>;
  onToggleDoneSection: (sectionKey: string) => void;
  onToggleDone: (id: string) => void;
  onSaveTitle: (id: string, value: string) => void;
  onSaveNotes: (id: string, value: string) => void;
  onCycleStatus: (id: string) => void;
  onCyclePriority: (id: string) => void;
  onCycleEffort: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string, phaseId: string) => void;
}) {
  const doneEntries = props.entries.filter((entry) => entry.item.status === "done");
  const openEntries = props.entries.filter((entry) => entry.item.status !== "done");
  const doneExpanded = props.expandedDoneSections.has(props.sectionKey);

  const rowProps = {
    onToggleDone: props.onToggleDone,
    onSaveTitle: props.onSaveTitle,
    onSaveNotes: props.onSaveNotes,
    onCycleStatus: props.onCycleStatus,
    onCyclePriority: props.onCyclePriority,
    onCycleEffort: props.onCycleEffort,
    onEdit: props.onEdit,
    onDelete: props.onDelete,
  };

  return (
    <>
      {doneEntries.length > 0 && (
        <div className="done-items-group">
          <button
            type="button"
            className="done-items-toggle"
            onClick={() => props.onToggleDoneSection(props.sectionKey)}
            aria-expanded={doneExpanded}
          >
            <span className="done-items-toggle-label">Closed</span>
            <span className="done-items-count">{doneEntries.length}</span>
            <span className="done-items-chevron" aria-hidden="true">{doneExpanded ? "▾" : "▸"}</span>
          </button>
          {doneExpanded && (
            <div className="items-list done-items-list">
              {doneEntries.map((entry) => (
                <RoadmapItemRow
                  key={entry.item.id}
                  item={entry.item}
                  phaseId={entry.phaseId}
                  {...rowProps}
                />
              ))}
            </div>
          )}
        </div>
      )}
      {openEntries.length > 0 && (
        <div className={`items-list${doneEntries.length > 0 ? " open-items-list" : ""}`}>
          {openEntries.map((entry) => (
            <RoadmapItemRow
              key={entry.item.id}
              item={entry.item}
              phaseId={entry.phaseId}
              {...rowProps}
            />
          ))}
        </div>
      )}
    </>
  );
}

function useMobileViewport(maxWidth = 600): boolean {
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia(`(max-width: ${maxWidth}px)`).matches;
  });

  useEffect(() => {
    const mediaQuery = window.matchMedia(`(max-width: ${maxWidth}px)`);
    const onChange = () => setIsMobile(mediaQuery.matches);
    mediaQuery.addEventListener("change", onChange);
    return () => mediaQuery.removeEventListener("change", onChange);
  }, [maxWidth]);

  return isMobile;
}

function RoadmapItemRow(props: {
  item: RoadmapItem;
  phaseId: string;
  onToggleDone: (id: string) => void;
  onSaveTitle: (id: string, value: string) => void;
  onSaveNotes: (id: string, value: string) => void;
  onCycleStatus: (id: string) => void;
  onCyclePriority: (id: string) => void;
  onCycleEffort: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string, phaseId: string) => void;
}) {
  const { item, phaseId } = props;
  const labels = itemLabels(item).filter(isItemLabel);
  const isMobile = useMobileViewport();

  function handleItemClick(event: React.MouseEvent<HTMLDivElement>) {
    if (!isMobile) return;
    const target = event.target as HTMLElement;
    if (target.closest(".item-check, .badge, .item-actions, .label-pill, button")) {
      return;
    }
    props.onEdit(item.id);
  }

  return (
    <div
      className={`item${item.status === "done" ? " done" : ""}${labels.length > 0 ? " item--has-labels" : ""}${isMobile ? " item--mobile-tap" : ""}`}
      onClick={handleItemClick}
    >
      <div
        className={`item-check${item.status === "done" ? " checked" : ""}`}
        onClick={() => props.onToggleDone(item.id)}
      />
      <div className="item-body">
        <div className="item-title-row">
          <span
            className="item-title"
            contentEditable={!isMobile}
            suppressContentEditableWarning
            spellCheck={false}
            onBlur={(event) => props.onSaveTitle(item.id, event.currentTarget.textContent ?? "")}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                event.currentTarget.blur();
              }
            }}
          >
            {item.title}
          </span>
        </div>
        <div className="item-meta">
          <MetaBadgeGroup
            label="Status"
            badgeClassName={`badge badge-status-${item.status}`}
            badgeText={statusLabel(item.status)}
            onClick={() => props.onCycleStatus(item.id)}
          />
          <span className="meta-inline-sep" aria-hidden="true">·</span>
          <MetaBadgeGroup
            label="Priority"
            badgeClassName={`badge badge-priority-${item.priority}`}
            badgeText={priorityLabel(item.priority)}
            onClick={() => props.onCyclePriority(item.id)}
          />
          <span className="meta-inline-sep" aria-hidden="true">·</span>
          <MetaBadgeGroup
            label="Effort"
            badgeClassName="badge badge-effort"
            badgeText={effortLabel(item.effort)}
            onClick={() => props.onCycleEffort(item.id)}
          />
        </div>
        <div
          className={`item-notes${item.notes ? "" : " empty"}`}
          contentEditable={!isMobile}
          suppressContentEditableWarning
          spellCheck={false}
          onBlur={(event) => props.onSaveNotes(item.id, event.currentTarget.textContent ?? "")}
          onFocus={(event) => {
            if (event.currentTarget.classList.contains("empty")) {
              event.currentTarget.textContent = "";
              event.currentTarget.classList.remove("empty");
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") event.currentTarget.blur();
          }}
        >
          {item.notes || "Add notes…"}
        </div>
      </div>
      <div className="item-actions">
        <button type="button" className="btn-text-edit" onClick={() => props.onEdit(item.id)}>
          Edit
        </button>
        <button
          type="button"
          className="icon-btn delete"
          title="Delete"
          onClick={() => props.onDelete(item.id, phaseId)}
        >
          ✕
        </button>
      </div>
      {labels.length > 0 && (
        <div className="item-labels">
          {labels.map((label) => (
            <LabelPill key={label} label={label} />
          ))}
        </div>
      )}
    </div>
  );
}
