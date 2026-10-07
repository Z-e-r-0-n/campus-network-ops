import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as Dialog from "@radix-ui/react-dialog";
import cytoscape from "cytoscape";
import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronRight,
  Clock3,
  Compass,
  FileCheck2,
  GitBranch,
  History,
  LayoutDashboard,
  LogOut,
  Network,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  SquareTerminal,
  TriangleAlert,
  X,
} from "lucide-react";
import { api, ApiError, label, RecordData, time } from "./api";
import "@fontsource-variable/dm-sans";
import "./style.css";
import { AccessPanel, AccessForm } from "./access";
import { InventoryPanel, InventoryForm, ExcludedDevices } from "./inventory";
import { ChecksPanel } from "./checks";
import { AIRoutes, DiagnosisPanel } from "./diagnosis";

type Page =
  | "Overview"
  | "Network"
  | "Incidents"
  | "Configuration review"
  | "History"
  | "Settings";
const pages: { name: Page; icon: typeof Network }[] = [
  { name: "Overview", icon: LayoutDashboard },
  { name: "Network", icon: Network },
  { name: "Incidents", icon: Activity },
  { name: "Configuration review", icon: FileCheck2 },
  { name: "History", icon: History },
  { name: "Settings", icon: Settings2 },
];
function Badge({ value }: { value: string }) {
  return (
    <span className={"badge " + value}>
      <i />
      {label(value)}
    </span>
  );
}
function Empty({
  icon: Icon = Compass,
  title,
  children,
  action,
}: {
  icon?: typeof Compass;
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon size={27} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
function Modal({
  title,
  description,
  open,
  onClose,
  children,
}: {
  title: string;
  description: string;
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(v) => !v && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <header>
            <div>
              <Dialog.Title>{title}</Dialog.Title>
              <Dialog.Description>{description}</Dialog.Description>
            </div>
            <Dialog.Close className="icon-btn" aria-label="Close">
              <X size={20} />
            </Dialog.Close>
          </header>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
function Field({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <label className="field">
      <span>{title}</span>
      {children}
    </label>
  );
}

function Login({
  configured,
  demo,
  onDone,
}: {
  configured: boolean;
  demo: boolean;
  onDone: () => void;
}) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const f = new FormData(event.currentTarget);
    try {
      const body = { username: f.get("username"), password: f.get("password") };
      if (!configured)
        await api("/setup/administrator", "POST", {
          ...body,
          bootstrap_token: f.get("token"),
        });
      await api("/session", "POST", body);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <div className="login-brand">
        <span className="brand-symbol">
          <Network />
        </span>
        <span>
          Campus<span className="muted"> / Network operations</span>
        </span>
      </div>
      <section className="login-layout">
        <div className="login-copy">
          <span className="eyebrow">A CLEAR VIEW OF YOUR INFRASTRUCTURE</span>
          <h1>
            Know the network.
            <br />
            <em>Act with context.</em>
          </h1>
          <p>
            Discover how your infrastructure connects, investigate service
            problems, and review each change before it runs.
          </p>
          <div className="login-points">
            <span>
              <GitBranch />
              Review the topology
            </span>
            <span>
              <Activity />
              Measure the paths that matter
            </span>
            <span>
              <ShieldCheck />
              Approve changes with recovery
            </span>
          </div>
          <div className="network-decoration" aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
            <div className="deco-center">
              <Network size={34} />
            </div>
          </div>
        </div>
        <form onSubmit={submit} className="login-card">
          <span className="eyebrow">
            {configured ? "YOUR WORKSPACE" : "FIRST INSTALLATION"}
          </span>
          <h2>{configured ? "Welcome back" : "Set up your workspace"}</h2>
          <p className="muted">
            {configured
              ? "Sign in to your network operations workspace."
              : "Create the administrator account. Device access is configured separately."}
          </p>
          {!configured && (
            <Field title="Local setup token">
              <input name="token" type="password" required autoComplete="off" />
              <small>
                Use the protected setup token prepared by the installation
                command.
              </small>
            </Field>
          )}
          <Field title="Username">
            <input
              name="username"
              required
              autoComplete="username"
              maxLength={64}
            />
          </Field>
          <Field title="Password">
            <input
              name="password"
              type="password"
              required
              minLength={configured ? 1 : 14}
              maxLength={256}
              autoComplete={configured ? "current-password" : "new-password"}
            />
          </Field>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary full" disabled={busy}>
            {busy
              ? "Please wait…"
              : configured
                ? "Sign in"
                : "Create account and sign in"}
            <ArrowRight size={17} />
          </button>
          <p className="login-note">
            <ShieldCheck size={16} />
            {demo
              ? "Disposable demo account. No campus access."
              : "No shared default password. Protected sessions."}
          </p>
        </form>
      </section>
      <footer>
        Campus network operations{" "}
        <span>Observe · Understand · Review · Verify</span>
      </footer>
    </main>
  );
}

function NetworkGraph({
  devices,
  edges,
  select,
}: {
  devices: RecordData[];
  edges: RecordData[];
  select: (d: RecordData) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!host.current || !devices.length) return;
    const addresses = new Map<string, string>();
    const ambiguous = new Set<string>();
    devices.forEach((d) =>
      (d.addresses ?? []).forEach((a: string) => {
        if (addresses.has(a)) ambiguous.add(a);
        addresses.set(a, d.id);
      }),
    );
    ambiguous.forEach((a) => addresses.delete(a));
    const validEdges = edges.filter(
      (e) =>
        devices.some((d) => d.id === e.source_id) &&
        e.decision !== "rejected" &&
        (e.target_id
          ? devices.some((d) => d.id === e.target_id)
          : addresses.has(e.remote_address)),
    );
    const cy = cytoscape({
      container: host.current,
      elements: [
        ...devices.map((d) => ({
          data: { id: d.id, label: d.label, status: d.status },
        })),
        ...validEdges.map((e) => ({
          data: {
            id: e.id,
            source: e.source_id,
            target: e.target_id ?? addresses.get(e.remote_address),
            label: label(e.layer),
            layer: e.layer,
          },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#e4ece4",
            "border-color": "#6d8d76",
            "border-width": 1.5,
            width: 24,
            height: 24,
            label: "data(label)",
            "font-size": 11,
            color: "#263c30",
            "text-valign": "bottom",
            "text-margin-y": 9,
            "text-wrap": "wrap",
            "text-max-width": "120px",
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.2,
            "line-color": "#c6d0c5",
            "curve-style": "bezier",
            "line-style": "dashed",
          },
        },
        {
          selector: "node:selected",
          style: {
            "background-color": "#3d6b51",
            "border-width": 4,
            "border-color": "#abc5ae",
          },
        },
      ],
      layout: {
        name: "breadthfirst",
        animate: false,
        directed: false,
        nodeDimensionsIncludeLabels: true,
        spacingFactor: 1.4,
        padding: 40,
      },
      minZoom: 0.3,
      maxZoom: 2,
    });
    cy.on("tap", "node", (event) => {
      const device = devices.find((d) => d.id === event.target.id());
      if (device) select(device);
    });
    return () => cy.destroy();
  }, [devices, edges, select]);
  return (
    <div
      className="graph"
      ref={host}
      role="img"
      aria-label="Observed network topology. Use the device table for keyboard navigation."
    />
  );
}

function App() {
  const [auth, setAuth] = useState<boolean | null>(null),
    [demo, setDemo] = useState(false),
    [configured, setConfigured] = useState(true),
    [user, setUser] = useState("");
  const [page, setPage] = useState<Page>(
    (decodeURIComponent(location.hash.slice(1)) || "Overview") as Page,
  );
  const [overview, setOverview] = useState<any>({
      counts: {},
      statuses: [],
      activity: [],
      runtime: [],
    }),
    [network, setNetwork] = useState<any>({ devices: [], edges: [] });
  const [items, setItems] = useState<RecordData[]>([]),
    [connectors, setConnectors] = useState<RecordData[]>([]),
    [profiles, setProfiles] = useState<RecordData[]>([]),
    [hosts, setHosts] = useState<RecordData[]>([]),
    [baselines, setBaselines] = useState<RecordData[]>([]),
    [inventoryChoices, setInventoryChoices] = useState<RecordData[]>([]),
    [inventoryRequests, setInventoryRequests] = useState<RecordData[]>([]),
    [holds, setHolds] = useState<RecordData[]>([]),
    [catalogue, setCatalogue] = useState<any>({
      checks: [],
      rules: [],
      runbooks: [],
    }),
    [readiness, setReadiness] = useState<any>({});
  const [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [modal, setModal] = useState(""),
    [selected, setSelected] = useState<RecordData | null>(null),
    [busy, setBusy] = useState(false),
    [networkTab, setNetworkTab] = useState("Devices");
  const changePage = (name: Page) => {
    setPage(name);
    location.hash = encodeURIComponent(name);
    setError("");
    setQuery("");
  };
  useEffect(() => {
    const onHash = () => {
      const p = decodeURIComponent(location.hash.slice(1));
      if (pages.some((x) => x.name === p)) setPage(p as Page);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const checkAuth = useCallback(async () => {
    const setup = await api("/setup/status");
    setConfigured(setup.configured);
    setDemo(setup.demo_mode === true);
    try {
      const session = await api("/session");
      setUser(session.actor);
      setAuth(true);
    } catch {
      setAuth(false);
    }
  }, []);
  useEffect(() => {
    checkAuth().catch((e) => {
      setError(e.message);
      setAuth(false);
    });
  }, [checkAuth]);
  const refresh = useCallback(async () => {
    if (!auth) return;
    try {
      const [summary, graph, con, cat, ready, profileData, hostData, holdData] =
        await Promise.all([
          api("/overview"),
          api("/network"),
          api("/connectors"),
          api("/catalogue"),
          api("/installation/readiness"),
          api("/access-profiles"),
          api("/host-identities"),
          api("/access-holds"),
        ]);
      setOverview(summary);
      setNetwork(graph);
      setConnectors(con.items);
      setCatalogue(cat);
      setReadiness(ready);
      setProfiles(profileData.items);
      setHosts(hostData.items);
      setHolds(holdData.items);
      if (page === "Network") {
        const [reviews, choices, requests] = await Promise.all([
          api("/baseline-drafts"),
          api("/inventory-options"),
          api("/inventory-requests"),
        ]);
        setBaselines(reviews.items);
        setInventoryChoices(choices.items);
        setInventoryRequests(requests.items);
      }
      const routes: Partial<Record<Page, string>> = {
        Incidents: "/incidents",
        "Configuration review": "/recommendations",
        History: "/history",
        Settings: "/connectors",
      };
      if (routes[page]) setItems((await api(routes[page]!)).items);
      if (page === "Network" && networkTab === "Discovery")
        setItems((await api("/discovery-runs")).items);
      setLoading(false);
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof ApiError && e.status === 401) setAuth(false);
      setLoading(false);
    }
  }, [auth, page, networkTab]);
  useEffect(() => {
    setLoading(true);
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);
  const showDevice = useCallback((device: RecordData) => {
    setSelected(device);
    setModal("device");
  }, []);
  const actionPending = useRef(false);
  async function act(operation: () => Promise<unknown>, close = true) {
    if (actionPending.current) return;
    actionPending.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
      if (close) {
        setModal("");
        setSelected(null);
      }
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      actionPending.current = false;
      setBusy(false);
    }
  }
  if (auth === null)
    return (
      <div className="boot">
        <Network size={30} />
        <p>Connecting to your workspace…</p>
      </div>
    );
  if (!auth)
    return <Login configured={configured} demo={demo} onDone={checkAuth} />;
  const counts = overview.counts ?? {},
    devices: RecordData[] = network.devices.filter((d: RecordData) =>
      `${d.label} ${(d.addresses ?? []).join(" ")} ${d.role}`
        .toLowerCase()
        .includes(query.toLowerCase()),
    );
  const accepted =
    overview.statuses.find(
      (s: any) => s.kind === "device" && s.status === "accepted",
    )?.count ?? 0;
  const pending = overview.statuses
    .filter((s: any) => s.kind === "device" && s.status !== "accepted")
    .reduce((n: number, s: any) => n + s.count, 0);
  const failed =
    overview.statuses.find(
      (s: any) => s.kind === "check_result" && s.status === "failed",
    )?.count ?? 0;
  const subtitles: Record<Page, string> = {
    Overview:
      "Current observations, work in progress and decisions that need you.",
    Network:
      "Explore connections, inspect evidence and close the gaps in coverage.",
    Incidents: "Measured problems, their evidence and the next useful action.",
    "Configuration review":
      "Improvements grounded in your accepted design and observed configuration.",
    History: "A record of what changed, who approved it and what was verified.",
    Settings: "Connections, access policies and installation readiness.",
  };
  const deviceDraft = modal === "device" ? selected : null;
  return (
    <div className="app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#Overview"
          onClick={() => changePage("Overview")}
        >
          <span className="brand-symbol">
            <Network size={21} />
          </span>
          <span>
            Campus<small>NETWORK OPERATIONS</small>
          </span>
        </a>
        <div className="workspace">
          <span className="workspace-mark">C</span>
          <div>
            Campus workspace<small>Infrastructure operations</small>
          </div>
        </div>
        <nav aria-label="Main navigation">
          {pages.map(({ name, icon: Icon }) => (
            <button
              key={name}
              className={page === name ? "active" : ""}
              onClick={() => changePage(name)}
            >
              <Icon size={18} />
              {name}
              {name === "Incidents" && counts.incident > 0 && (
                <b>{counts.incident}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="access-note">
            <ShieldCheck size={17} />
            <span>
              Changes require approval
              <small>Exact scope. Recorded outcomes.</small>
            </span>
          </div>
          <button
            className="user"
            onClick={() =>
              act(async () => {
                await api("/session", "DELETE");
                setAuth(false);
              })
            }
          >
            <span className="avatar">{user[0]?.toUpperCase()}</span>
            <span>
              {user}
              <small>Administrator</small>
            </span>
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <span>
            Workspace <ChevronRight size={14} /> <strong>{page}</strong>
          </span>
          <div>
            <span className="muted small">{time(overview.at)}</span>
            <button
              className="icon-btn"
              aria-label="Refresh observations"
              onClick={refresh}
            >
              <RefreshCw size={16} />
            </button>
          </div>
        </header>
        <main className="content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">NETWORK OPERATIONS</span>
              <h1>{page === "Overview" ? "Network overview" : page}</h1>
              <p>{subtitles[page]}</p>
            </div>
            <div className="heading-actions">
              {page === "Network" && (
                <button
                  onClick={() => {
                    setSelected(null);
                    setModal("add-device");
                  }}
                >
                  <Plus size={16} />
                  Add device
                </button>
              )}
              {["Overview", "Network"].includes(page) && (
                <button
                  className="primary"
                  onClick={() => setModal("discover")}
                >
                  <Compass size={17} />
                  Discover network
                </button>
              )}
              {page === "Configuration review" && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    act(() => api("/configuration-reviews", "POST", {}))
                  }
                >
                  <RefreshCw size={16} />
                  Run review
                </button>
              )}
              {page === "Settings" && (
                <button
                  className="primary"
                  onClick={() => setModal("connector")}
                >
                  <Plus size={17} />
                  Add connection
                </button>
              )}
            </div>
          </div>
          {error && (
            <div className="error-banner" role="alert">
              <TriangleAlert size={18} />
              {error}
              <button
                className="icon-btn"
                onClick={() => setError("")}
                aria-label="Dismiss error"
              >
                <X size={16} />
              </button>
            </div>
          )}
          {loading && (
            <div className="loading-line" role="status">
              Updating workspace…
            </div>
          )}
          {page === "Overview" && (
            <>
              <div className="metric-grid">
                <Metric
                  title="Observed devices"
                  value={counts.device ?? 0}
                  detail={`${accepted} reviewed · ${pending} need review`}
                  icon={Network}
                />
                <Metric
                  title="Failed checks"
                  value={failed}
                  detail={
                    counts.check_result
                      ? "Inspect source and measurement time"
                      : "Checks have not produced results yet"
                  }
                  icon={Activity}
                />
                <Metric
                  title="Configuration findings"
                  value={counts.recommendation ?? 0}
                  detail="From observed settings and accepted policy"
                  icon={FileCheck2}
                />
                <Metric
                  title="Recorded changes"
                  value={counts.execution ?? 0}
                  detail="Review execution and recovery outcomes"
                  icon={History}
                />
              </div>
              <div className="overview-grid">
                <section className="panel network-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">INFRASTRUCTURE</span>
                      <h2>Your network</h2>
                    </div>
                    <button
                      className="text-btn"
                      onClick={() => changePage("Network")}
                    >
                      Explore network
                      <ArrowUpRight size={16} />
                    </button>
                  </div>
                  {network.devices.length ? (
                    <>
                      <NetworkGraph
                        devices={network.devices}
                        edges={network.edges}
                        select={showDevice}
                      />
                      <div className="panel-foot">
                        <span>
                          <i className="legend-dot" />
                          Observed devices
                        </span>
                        <span>Dashed connections are advertised neighbors</span>
                      </div>
                    </>
                  ) : (
                    <Empty
                      title="Start with a known device"
                      action={
                        <button onClick={() => setModal("discover")}>
                          Set discovery scope
                          <ArrowRight size={16} />
                        </button>
                      }
                    >
                      Connect your monitoring source, choose a seed and review
                      what the system discovers. Missing access remains visible.
                    </Empty>
                  )}
                </section>
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">NEXT STEPS</span>
                      <h2>Bring the network into view</h2>
                    </div>
                  </div>
                  <div className="steps">
                    {[
                      {
                        n: "01",
                        title: "Connect existing sources",
                        text: "Use your NMS and infrastructure access.",
                        done: connectors.length > 0,
                        click: () => changePage("Settings"),
                      },
                      {
                        n: "02",
                        title: "Discover and review",
                        text: "Resolve identities and correct connections.",
                        done: accepted > 0,
                        click: () => changePage("Network"),
                      },
                      {
                        n: "03",
                        title: "Establish dedicated access",
                        text: "Verify accounts, keys and recovery paths.",
                        done:
                          network.devices.length > 0 &&
                          network.devices.every(
                            (d: RecordData) =>
                              d.access === "dedicated_verified",
                          ),
                        click: () => {
                          changePage("Network");
                          setNetworkTab("Access");
                        },
                      },
                      {
                        n: "04",
                        title: "Start the right checks",
                        text: "Measure paths and services with known sources.",
                        done: counts.check_result > 0,
                        click: () => {
                          changePage("Network");
                          setNetworkTab("Checks");
                        },
                      },
                    ].map((s) => (
                      <button className="step" key={s.n} onClick={s.click}>
                        <span
                          className={
                            s.done ? "step-number done" : "step-number"
                          }
                        >
                          {s.done ? <Check size={16} /> : s.n}
                        </span>
                        <span>
                          <strong>{s.title}</strong>
                          <small>{s.text}</small>
                        </span>
                        <ChevronRight size={16} />
                      </button>
                    ))}
                  </div>
                  <div className="soft-note">
                    <ShieldCheck size={19} />
                    <p>
                      Discovery and review come first. A repair runs only after
                      you approve its complete plan.
                    </p>
                  </div>
                </section>
                <section className="panel wide">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">WORKSPACE ACTIVITY</span>
                      <h2>Running and recent work</h2>
                    </div>
                    <button
                      className="text-btn"
                      onClick={() => changePage("History")}
                    >
                      View history
                      <ArrowUpRight size={16} />
                    </button>
                  </div>
                  {overview.activity.length ? (
                    <div className="activity-list">
                      {overview.activity.map((item: RecordData) => (
                        <button
                          key={item.id}
                          onClick={() => {
                            setSelected(item);
                            setModal("detail");
                          }}
                        >
                          <span className="activity-icon">
                            <GitBranch size={17} />
                          </span>
                          <div>
                            <strong>
                              {item.seeds
                                ? "Network discovery"
                                : item.plan_id
                                  ? "Approved change"
                                  : "Configuration review"}
                            </strong>
                            <small>
                              {time(item.started_at ?? item.created_at)}
                            </small>
                          </div>
                          <Badge value={item.status} />
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="inline-empty">
                      <Clock3 size={20} />
                      <span>
                        No jobs have run yet. Work will appear here as you set
                        up sources and checks.
                      </span>
                    </div>
                  )}
                </section>
              </div>
            </>
          )}
          {page === "Network" && (
            <>
              <div className="tabs" role="tablist" aria-label="Network views">
                {[
                  "Devices",
                  "Connections",
                  "Checks",
                  "Access",
                  "Discovery",
                  "Review",
                ].map((tab) => (
                  <button
                    role="tab"
                    aria-selected={networkTab === tab}
                    key={tab}
                    className={networkTab === tab ? "selected" : ""}
                    onClick={() => setNetworkTab(tab)}
                  >
                    {tab}
                  </button>
                ))}
              </div>
              {networkTab === "Devices" && (
                <section className="panel">
                  <div className="table-tools">
                    <div className="search">
                      <Search size={17} />
                      <input
                        placeholder="Search devices, roles or addresses"
                        aria-label="Search devices"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                    </div>
                    <span className="muted small">
                      {devices.length} visible devices
                    </span>
                  </div>
                  {devices.length ? (
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Device</th>
                            <th>Role</th>
                            <th>Address</th>
                            <th>Review</th>
                            <th>Last measurement</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {devices.map((d) => (
                            <tr key={d.id}>
                              <td>
                                <button
                                  className="table-link"
                                  onClick={() => showDevice(d)}
                                >
                                  <span className="device-icon">
                                    <Network size={17} />
                                  </span>
                                  {d.label}
                                </button>
                              </td>
                              <td>{label(d.role ?? "unknown")}</td>
                              <td className="mono">
                                {d.addresses?.join(", ") || "Not assigned"}
                              </td>
                              <td>
                                <Badge value={d.status} />
                              </td>
                              <td>{time(d.observed_at)}</td>
                              <td>
                                <button
                                  className="icon-btn"
                                  aria-label={"Inspect " + d.label}
                                  onClick={() => showDevice(d)}
                                >
                                  <ArrowUpRight size={17} />
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <Empty
                      title={
                        query
                          ? "No matching devices"
                          : "No infrastructure discovered yet"
                      }
                    >
                      Start discovery or add a known device. The table preserves
                      review status and observation times.
                    </Empty>
                  )}
                  <ExcludedDevices
                    refreshKey={overview.at ?? ""}
                    open={(mode, item) => {
                      setSelected(item ?? null);
                      setModal(mode);
                    }}
                  />
                </section>
              )}
              {networkTab === "Connections" && (
                <section className="panel">
                  <div className="panel-heading">
                    <h2>Network connections</h2>
                    <button
                      disabled={network.devices.length < 2}
                      onClick={() => {
                        setSelected(null);
                        setModal("add-connection");
                      }}
                    >
                      <Plus size={16} /> Add connection
                    </button>
                  </div>
                  {network.devices.length ? (
                    <>
                      <NetworkGraph
                        devices={network.devices}
                        edges={network.edges}
                        select={showDevice}
                      />
                      <div className="activity-list">
                        {network.edges.map((edge: RecordData) => (
                          <button
                            key={edge.id}
                            onClick={() => {
                              setSelected(edge);
                              setModal("connection");
                            }}
                          >
                            <div>
                              <strong>
                                {network.devices.find(
                                  (d: RecordData) => d.id === edge.source_id,
                                )?.label ?? "Unknown source"}{" "}
                                →{" "}
                                {network.devices.find(
                                  (d: RecordData) => d.id === edge.target_id,
                                )?.label ??
                                  edge.remote_address ??
                                  "Unresolved neighbor"}
                              </strong>
                              <small>
                                {label(edge.layer)} ·{" "}
                                {edge.local_port || "Unknown port"} /{" "}
                                {edge.remote_port || "Unknown port"}
                              </small>
                            </div>
                            <Badge value={edge.decision ?? edge.status} />
                          </button>
                        ))}
                      </div>
                      <div className="panel-foot">
                        Advertised neighbors are distinct from physically
                        verified links. Select a device or use the Devices
                        table.
                      </div>
                    </>
                  ) : (
                    <Empty title="Connections will appear after discovery">
                      The map will distinguish observed, accepted and unknown
                      connections.
                    </Empty>
                  )}
                </section>
              )}
              {networkTab === "Review" && (
                <InventoryPanel
                  baselines={baselines}
                  connectors={connectors}
                  requests={inventoryRequests}
                  busy={busy}
                  act={act}
                  open={(mode, item) => {
                    setSelected(item ?? null);
                    setModal(mode);
                  }}
                />
              )}
              {networkTab === "Access" && (
                <AccessPanel
                  profiles={profiles}
                  hosts={hosts}
                  holds={holds}
                  open={(mode, item) => {
                    setSelected(item ?? null);
                    setModal(mode);
                  }}
                />
              )}
              {networkTab === "Checks" && (
                <ChecksPanel devices={network.devices} />
              )}
              {networkTab === "Discovery" && (
                <section className="panel">
                  <div className="panel-heading">
                    <h2>Discovery runs</h2>
                  </div>
                  {items.length ? (
                    <div className="activity-list">
                      {items.map((item) => (
                        <button
                          key={item.id}
                          onClick={() => {
                            setSelected(item);
                            setModal("detail");
                          }}
                        >
                          <span className="activity-icon">
                            <Activity size={17} />
                          </span>
                          <div>
                            <strong>{item.seeds?.join(", ")}</strong>
                            <small>{time(item.started_at)}</small>
                          </div>
                          <Badge value={item.status} />
                        </button>
                      ))}
                    </div>
                  ) : (
                    <Empty title="No discovery runs yet">
                      Set a seed and permitted scope to start a bounded
                      discovery.
                    </Empty>
                  )}
                </section>
              )}
            </>
          )}
          {["Incidents", "Configuration review"].includes(page) && (
            <section className="panel">
              <div className="panel-heading">
                <h2>
                  {page === "Incidents"
                    ? "Cases requiring attention"
                    : "Review recommendations"}
                </h2>
                <span className="count-pill">{items.length}</span>
              </div>
              {items.length ? (
                <div className="finding-list">
                  {items.map((item) => (
                    <button
                      key={item.id}
                      className="finding"
                      onClick={() => {
                        setSelected(item);
                        setModal("finding");
                      }}
                    >
                      <span className="finding-icon">
                        {page === "Incidents" ? (
                          <Activity size={21} />
                        ) : (
                          <SlidersHorizontal size={21} />
                        )}
                      </span>
                      <div>
                        <span className="eyebrow">
                          {item.rule_id ?? "MEASURED SYMPTOM"}
                        </span>
                        <h3>{item.title}</h3>
                        <p>{item.summary}</p>
                        <small>Updated {time(item.updated_at)}</small>
                      </div>
                      <Badge value={item.status} />
                      <ChevronRight size={17} />
                    </button>
                  ))}
                </div>
              ) : (
                <Empty
                  icon={page === "Incidents" ? CheckCheck : FileCheck2}
                  title={
                    page === "Incidents"
                      ? "No incidents recorded"
                      : "No configuration findings yet"
                  }
                >
                  {page === "Incidents"
                    ? "This is an empty case list, not a claim of complete network health. Check measurement coverage in Network."
                    : "A review uses observed configuration and accepted policies. Missing evidence is shown as a coverage gap."}
                </Empty>
              )}
            </section>
          )}
          {page === "History" && (
            <section className="panel">
              <div className="panel-heading">
                <h2>Activity record</h2>
                <span className="muted small">
                  Chronological · Original timestamps retained
                </span>
              </div>
              {items.length ? (
                <div className="timeline">
                  {items.map((item: any) => (
                    <div key={item.seq}>
                      <span className="timeline-dot" />
                      <div>
                        <strong>{label(item.action)}</strong>
                        <p>
                          {item.actor} · {item.target}
                        </p>
                      </div>
                      <time>{time(item.at)}</time>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty icon={History} title="No activity recorded">
                  Sign-ins, reviewed corrections, approvals and verified
                  outcomes are recorded here.
                </Empty>
              )}
            </section>
          )}
          {page === "Settings" && (
            <div className="settings-grid">
              <AIRoutes connectors={connectors} />
              <section className="panel">
                <div className="panel-heading">
                  <h2>Connected sources</h2>
                  <span className="count-pill">{connectors.length}</span>
                </div>
                {connectors.length ? (
                  <div className="connector-list">
                    {connectors.map((c) => (
                      <div key={c.id}>
                        <span className="connector-symbol">
                          <SquareTerminal size={21} />
                        </span>
                        <div>
                          <strong>{c.name}</strong>
                          <small>
                            {c.kind} · {c.endpoint}
                          </small>
                          <Badge value={c.status} />
                        </div>
                        <button
                          disabled={busy}
                          onClick={() =>
                            act(
                              () =>
                                api(
                                  "/connectors/" + c.id + "/validate",
                                  "POST",
                                  {},
                                ),
                              false,
                            )
                          }
                        >
                          Test connection
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty
                    icon={SquareTerminal}
                    title="Connect what is already there"
                    action={
                      <button onClick={() => setModal("connector")}>
                        <Plus size={16} />
                        Add connection
                      </button>
                    }
                  >
                    Add LibreNMS, intended inventory, configuration history or
                    the AI gateway. Credentials stay outside application
                    records.
                  </Empty>
                )}
              </section>
              <section className="panel">
                <div className="panel-heading">
                  <h2>Installation readiness</h2>
                </div>
                <dl className="readiness">
                  <div>
                    <dt>Application database</dt>
                    <dd>
                      <Badge value={readiness.database ?? "unknown"} />
                    </dd>
                  </div>
                  <div>
                    <dt>Observation worker</dt>
                    <dd>
                      <Badge
                        value={
                          readiness.workers?.observation_worker?.status ??
                          "not_connected"
                        }
                      />
                    </dd>
                  </div>
                  <div>
                    <dt>Execution worker</dt>
                    <dd>
                      <Badge
                        value={
                          readiness.workers?.execution_worker?.status ??
                          "not_connected"
                        }
                      />
                    </dd>
                  </div>
                  <div>
                    <dt>Campus changes</dt>
                    <dd>
                      <Badge
                        value={
                          readiness.mutations_enabled
                            ? "enabled"
                            : "not_commissioned"
                        }
                      />
                    </dd>
                  </div>
                </dl>
                <div className="soft-note">
                  <ShieldCheck size={19} />
                  <p>
                    Unqualified actions remain blocked. Source access and
                    recovery readiness are verified before enabling changes.
                  </p>
                </div>
                <div className="panel-actions">
                  <button onClick={() => setModal("password")}>
                    Change your password
                  </button>
                  <button
                    onClick={() =>
                      act(() => api("/execution-pause", "POST", {}), false)
                    }
                  >
                    Pause new changes
                  </button>
                </div>
              </section>
            </div>
          )}
          <footer className="content-footer">
            <span>
              <i className="legend-dot" />
              Evidence keeps its source and time
            </span>
            <span>Changes are reviewed before execution</span>
          </footer>
        </main>
      </div>
      <Modal
        title={
          (
            {
              "baseline-prepare": "Prepare inventory review",
              "baseline-review": "Review intended inventory",
              "identity-merge": "Merge a verified alias",
              "identity-split": "Split a mistaken identity",
              "identity-exclude": "Exclude an identity",
              "identity-restore": "Restore an excluded identity",
              "inventory-object": "Add inventory details",
              connection: "Review connection",
              "add-connection": "Add a missing connection",
              reauthenticate: "Confirm your identity",
            } as Record<string, string>
          )[modal] ??
          (modal.startsWith("access-")
            ? ({
                "access-profile": "Create an access profile",
                "access-profile-detail": selected?.name ?? "Access profile",
                "access-hold": "Review failed access",
              }[modal] ?? "Access")
            : modal === "host-scan"
              ? "Collect a device fingerprint"
              : modal === "host-review"
                ? "Review device identity"
                : modal === "discover"
                  ? "Discover your network"
                  : modal === "connector"
                    ? "Add a connection"
                    : modal === "add-device"
                      ? "Add a missing device"
                      : modal === "device"
                        ? (selected?.label ?? "Device")
                        : modal === "check"
                          ? "Configure a scheduled check"
                          : modal === "password"
                            ? "Change your password"
                            : (selected?.title ?? "Work details"))
        }
        description={
          modal === "discover"
            ? "Start from a known infrastructure address and define where discovery may go."
            : modal === "connector"
              ? "Connect an existing source with a protected credential reference."
              : modal === "device"
                ? "Inspect the identity, source evidence and access state."
                : "Review the information below."
        }
        open={!!modal}
        onClose={() => {
          setModal("");
          setSelected(null);
          setError("");
        }}
      >
        {error && (
          <div className="error" role="alert">
            <p>{error}</p>
            {error.includes("Confirm your password") && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  act(
                    () =>
                      api("/session/reauthenticate", "POST", {
                        password: f.get("password"),
                      }),
                    false,
                  );
                }}
              >
                <label>
                  Current password
                  <input
                    type="password"
                    name="password"
                    required
                    autoComplete="current-password"
                  />
                </label>
                <button disabled={busy}>Confirm identity</button>
              </form>
            )}
          </div>
        )}
        <InventoryForm
          key={modal}
          mode={modal}
          selected={selected}
          devices={network.devices}
          edges={network.edges}
          connectors={connectors}
          choices={inventoryChoices}
          busy={busy}
          act={act}
        />
        <AccessForm
          mode={modal}
          profiles={profiles}
          selected={selected}
          busy={busy}
          act={act}
        />
        {modal === "discover" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(() =>
                api("/discovery-runs", "POST", {
                  seeds: String(f.get("seed"))
                    .split(",")
                    .map((v) => v.trim()),
                  scope: {
                    prefixes: String(f.get("scope"))
                      .split(",")
                      .map((v) => v.trim()),
                  },
                  connector_id: f.get("connector") || null,
                  access_profile_ids: f.getAll("access_profile"),
                }),
              );
            }}
          >
            <Field title="Seed IP address">
              <input name="seed" required placeholder="192.0.2.1" />
            </Field>
            <Field title="Allowed management prefixes">
              <input
                name="scope"
                required
                placeholder="192.0.2.0/24, 198.51.100.0/24"
              />
              <small>
                Use reviewed infrastructure ranges. External neighbors remain
                outside discovery.
              </small>
            </Field>
            <Field title="Monitoring source">
              <select name="connector" defaultValue="">
                <option value="">None — use direct access</option>
                {connectors
                  .filter((c) => c.kind === "librenms")
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </Field>
            <fieldset className="profile-selection">
              <legend>Key-based access profiles</legend>
              {profiles.map((p) => (
                <label className="checkbox-field" key={p.id}>
                  <input type="checkbox" name="access_profile" value={p.id} />
                  <span>
                    {p.name}
                    <small>{p.prefixes.join(", ")}</small>
                  </span>
                </label>
              ))}
              {!profiles.length && (
                <p>
                  No access profiles yet. Add one from Network → Access.
                  Discovery without a source records the seed and its access
                  gap.
                </p>
              )}
            </fieldset>
            <div className="dialog-actions">
              <button type="button" onClick={() => setModal("")}>
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                Start discovery
                <ArrowRight size={16} />
              </button>
            </div>
          </form>
        )}
        {modal === "connector" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(() =>
                api("/connectors", "POST", {
                  ...Object.fromEntries(f),
                  source_timezone_verified:
                    f.get("source_timezone_verified") === "on",
                  allow_private_http: f.get("allow_private_http") === "on",
                }),
              );
            }}
          >
            <Field title="Connection name">
              <input name="name" required placeholder="Campus monitoring" />
            </Field>
            <Field title="Source">
              <select name="kind">
                <option value="librenms">LibreNMS</option>
                <option value="netbox">NetBox</option>
                <option value="omniroute">OmniRoute</option>
                <option value="oxidized">Oxidized</option>
              </select>
            </Field>
            <Field title="API base URL">
              <input
                name="endpoint"
                type="url"
                required
                placeholder="https://monitoring.example/api/v0"
              />
            </Field>
            <Field title="Verified server IP">
              <input name="pinned_address" required />
              <small>
                Requests are pinned to this address and do not follow redirects.
              </small>
            </Field>
            <Field title="Protected credential reference">
              <input name="secret_ref" required placeholder="librenms_token" />
              <small>
                Reference a private file provisioned by the installer. Do not
                enter the token here.
              </small>
            </Field>
            <Field title="Source timezone">
              <input name="source_timezone" required defaultValue="UTC" />
            </Field>
            <label className="checkbox-field">
              <input type="checkbox" name="source_timezone_verified" />
              <span>
                I verified the timezone used by this source.
                <small>Unzoned timestamps remain unknown until verified.</small>
              </span>
            </label>
            <label className="checkbox-field">
              <input type="checkbox" name="allow_private_http" />
              <span>
                Allow HTTP for this private-network source.
                <small>
                  Credentials cross that connection without transport
                  encryption. Prefer HTTPS.
                </small>
              </span>
            </label>
            <div className="dialog-actions">
              <button className="primary" disabled={busy}>
                Save connection
              </button>
            </div>
          </form>
        )}
        {(modal === "add-device" || modal === "device") && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              const body = {
                label: f.get("label"),
                role: f.get("role"),
                addresses: String(f.get("addresses"))
                  .split(",")
                  .map((v) => v.trim())
                  .filter(Boolean),
                location: f.get("location"),
                reason: f.get("reason"),
              };
              act(() =>
                api(
                  "/devices" + (deviceDraft ? "/" + deviceDraft.id : ""),
                  deviceDraft ? "PATCH" : "POST",
                  body,
                  deviceDraft?.revision,
                ),
              );
            }}
          >
            {deviceDraft && (
              <div className="detail-status">
                <Badge value={deviceDraft.status} />
                <span className="muted small">
                  Last observation: {time(deviceDraft.observed_at)}
                </span>
              </div>
            )}
            <Field title="Device name">
              <input
                name="label"
                required
                defaultValue={deviceDraft?.label ?? ""}
              />
            </Field>
            <Field title="Role">
              <select name="role" defaultValue={deviceDraft?.role ?? "unknown"}>
                {[
                  "unknown",
                  "core",
                  "distribution",
                  "access",
                  "router",
                  "firewall",
                  "server",
                  "probe",
                  "unmanaged",
                ].map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </Field>
            <Field title="Management addresses">
              <input
                name="addresses"
                defaultValue={deviceDraft?.addresses?.join(", ") ?? ""}
              />
            </Field>
            <Field title="Location">
              <input
                name="location"
                defaultValue={deviceDraft?.location ?? ""}
              />
            </Field>
            <Field title="Reason for the record or correction">
              <textarea name="reason" required minLength={3} />
            </Field>
            {deviceDraft && (
              <div className="notice">
                Automation access:{" "}
                {label(deviceDraft.access ?? "not_configured")}. Corrections
                remain drafts until the baseline is reviewed and published.
              </div>
            )}
            {deviceDraft && (
              <div className="toolbar-actions">
                <button
                  type="button"
                  disabled={!!deviceDraft.netbox}
                  onClick={() => setModal("identity-merge")}
                >
                  Merge alias
                </button>
                <button
                  type="button"
                  disabled={
                    !!deviceDraft.netbox || deviceDraft.addresses?.length < 2
                  }
                  onClick={() => setModal("identity-split")}
                >
                  Split addresses
                </button>
                <button
                  type="button"
                  onClick={() => setModal("identity-exclude")}
                >
                  Exclude from view
                </button>
              </div>
            )}
            <div className="dialog-actions">
              <button className="primary" disabled={busy}>
                Save draft
              </button>
            </div>
          </form>
        )}
        {modal === "password" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(() =>
                api(
                  "/account/password",
                  "POST",
                  Object.fromEntries(new FormData(e.currentTarget)),
                ),
              );
            }}
          >
            <Field title="Current password">
              <input
                type="password"
                name="password"
                required
                autoComplete="current-password"
              />
            </Field>
            <Field title="New password">
              <input
                type="password"
                name="new_password"
                required
                minLength={14}
                autoComplete="new-password"
              />
            </Field>
            <div className="dialog-actions">
              <button className="primary" disabled={busy}>
                Change password
              </button>
            </div>
          </form>
        )}
        {["detail", "finding"].includes(modal) && selected && (
          <>
            <div className="detail-status">
              <Badge value={selected.status ?? "recorded"} />
              <span>{time(selected.observed_at ?? selected.created_at)}</span>
            </div>
            {selected.summary && <p>{selected.summary}</p>}
            {selected.gaps?.length > 0 && (
              <div className="gap-list">
                <h3>Discovery gaps</h3>
                {selected.gaps.map((g: any, i: number) => (
                  <p key={i}>
                    <strong>{g.address}</strong> {g.reason}
                  </p>
                ))}
              </div>
            )}
            <details>
              <summary>Inspect recorded evidence</summary>
              <pre>{JSON.stringify(selected, null, 2)}</pre>
            </details>
            {(modal === "finding" || selected.policy_id) && (
              <DiagnosisPanel
                kind={modal === "finding" ? "recommendation" : "incident"}
                subjectId={selected.id}
              />
            )}
            {modal === "finding" && (
              <div className="dialog-actions">
                <button
                  disabled={busy}
                  onClick={() =>
                    act(() =>
                      api(
                        "/recommendations/" + selected.id + "/disposition",
                        "POST",
                        {
                          status: "deferred",
                          reason: "Administrator deferred for review",
                          until: new Date(
                            Date.now() + 7 * 86400000,
                          ).toISOString(),
                        },
                        selected.revision,
                      ),
                    )
                  }
                >
                  Defer for one week
                </button>
              </div>
            )}
          </>
        )}
      </Modal>
    </div>
  );
}
function Metric({
  title,
  value,
  detail,
  icon: Icon,
}: {
  title: string;
  value: number;
  detail: string;
  icon: typeof Network;
}) {
  return (
    <section className="metric">
      <div>
        <span>{title}</span>
        <Icon size={18} />
      </div>
      <strong>{String(value).padStart(2, "0")}</strong>
      <small>{detail}</small>
    </section>
  );
}
function DemoNotice() {
  const [demo, setDemo] = useState(false);
  const banner = useRef<HTMLElement>(null);
  useEffect(() => {
    api("/setup/status")
      .then((s) => setDemo(s.demo_mode === true))
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!demo || !banner.current) return;
    const observer = new ResizeObserver(([entry]) => {
      document.documentElement.style.setProperty(
        "--demo-height",
        `${entry.target.getBoundingClientRect().height}px`,
      );
    });
    observer.observe(banner.current);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty("--demo-height");
    };
  }, [demo]);
  if (!demo) return null;
  return (
    <aside ref={banner} className="demo-banner" aria-label="Demonstration mode">
      <strong>Interactive development demo · Sample topology</strong>
      <span>
        Edit drafts and inspect reviews. No scans, live monitoring, publication
        or repairs run here.
      </span>
      <small>
        Demo sign-in: demo / Explore-the-workflow-123 · Changes reset when the
        demo restarts.
      </small>
    </aside>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DemoNotice />
    <App />
  </React.StrictMode>,
);
