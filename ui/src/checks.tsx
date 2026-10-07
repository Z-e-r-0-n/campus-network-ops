import { useCallback, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Activity,
  ArrowRight,
  Clock3,
  Pause,
  Play,
  Plus,
  Radio,
  Settings2,
  X,
} from "lucide-react";
import { api, label, time, type RecordData } from "./api";

const titles: Record<string, string> = {
  H01: "Path reachability",
  H02: "Latency distribution",
  H03: "Packet loss",
  H04: "Service response",
};
const reasons: Record<string, string> = {
  policy_disabled: "This check is paused.",
  unreviewed_path: "The source or destination needs inventory review.",
  probe_or_approval_needs_review:
    "The source interface or approved path changed. Verify the source and review this check again.",
  policy_or_path_changed_during_collection:
    "The path or policy changed during collection; this result cannot establish health.",
  source_capability_or_measurement_unavailable:
    "A verified source is needed before measurements can run.",
  measurement_budget_busy:
    "Another measurement is using this source or destination. This check will retry after the current slot.",
};
function Badge({ value }: { value: string }) {
  return (
    <span className={`badge ${value}`}>
      <i />
      {label(value)}
    </span>
  );
}
function number(value: unknown) {
  return typeof value === "number"
    ? value.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : "—";
}
function Metric({
  title,
  value,
  unit = "",
}: {
  title: string;
  value: unknown;
  unit?: string;
}) {
  return (
    <div className="measurement-metric">
      <small>{title}</small>
      <strong>
        {number(value)} <span>{unit}</span>
      </strong>
    </div>
  );
}

export function ChecksPanel({
  devices: initialDevices,
}: {
  devices: RecordData[];
}) {
  const [devices, setDevices] = useState(initialDevices);
  const [rows, setRows] = useState<RecordData[]>([]),
    [probes, setProbes] = useState<RecordData[]>([]);
  const [requests, setRequests] = useState<RecordData[]>([]),
    [error, setError] = useState("");
  const [cursor, setCursor] = useState(""),
    [next, setNext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [loaded, setLoaded] = useState(false);
  const [mode, setMode] = useState(""),
    [selected, setSelected] = useState<RecordData | null>(null);
  const [history, setHistory] = useState<RecordData[]>([]);
  const pending = useRef(false);
  useEffect(() => {
    let cancelled = false;
    async function identities() {
      let after = "";
      const found: RecordData[] = [];
      do {
        const page = await api(
          `/devices?limit=1000&after=${encodeURIComponent(after)}`,
        );
        if (cancelled) return;
        found.push(
          ...page.items.filter((d: RecordData) => d.status === "accepted"),
        );
        after = page.next_cursor ?? "";
      } while (after);
      setDevices(found);
    }
    identities().catch((e) => {
      if (!cancelled) setError(e.message);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  const refresh = useCallback(async () => {
    const [measurements, sources, recent] = await Promise.all([
      api(`/measurements?after=${encodeURIComponent(cursor)}`),
      api("/probes?limit=1000"),
      api("/probe-requests?limit=1000"),
    ]);
    setRows(measurements.items);
    setNext(measurements.next_cursor);
    setProbes(sources.items);
    setRequests(recent.items);
    setLoaded(true);
  }, [cursor]);
  useEffect(() => {
    let live = true;
    const update = () =>
      refresh().catch((e) => {
        if (live) setError(e.message);
      });
    update();
    const timer = setInterval(update, 15000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [refresh]);
  async function act(operation: () => Promise<unknown>, close = true) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
      if (close) {
        setMode("");
        setSelected(null);
      }
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  const accepted = devices.filter((d) => d.status === "accepted");
  const open = (value: string, record: RecordData | null = null) => {
    setError("");
    setSelected(record);
    setMode(value);
  };
  const approved = rows.filter((r) => r.enabled).length;
  return (
    <div className="measurements-workspace">
      <section className="measurement-intro">
        <div>
          <span className="eyebrow">Observe the actual path</span>
          <h2>Every measurement has a point of view.</h2>
          <p>
            Choose where traffic starts, what it should reach, and the limits
            you accept. Results keep their source, destination and observation
            window.
          </p>
        </div>
        <div className="measurement-summary">
          <Radio size={22} />
          <strong>{probes.filter((p) => p.verified_local).length}</strong>
          <span>verified local sources</span>
        </div>
        <div className="measurement-summary">
          <Activity size={22} />
          <strong>{approved}</strong>
          <span>active checks on this page</span>
        </div>
      </section>
      {error && !mode && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Measurement sources</h2>
            <p>
              Local probes run from an address assigned to the observation
              worker.
            </p>
          </div>
          <button onClick={() => open("probe")} disabled={!accepted.length}>
            <Radio size={16} /> Verify a source
          </button>
        </div>
        {!accepted.length && (
          <div className="panel-foot">
            Accept your source and destination identities in Inventory review to
            configure measurements.
          </div>
        )}
        <div className="probe-list">
          {probes.length ? (
            probes.map((p) => (
              <div className="probe-row" key={p.id}>
                <Radio size={18} />
                <div>
                  <strong>
                    {devices.find((d) => d.id === p.id)?.label ??
                      p.source_address}
                  </strong>
                  <small>
                    {p.source_address} ·{" "}
                    {p.binding?.interface ?? "Waiting for verification"}
                  </small>
                </div>
                <Badge
                  value={
                    p.status ?? (p.verified_local ? "verified" : "unknown")
                  }
                />
              </div>
            ))
          ) : (
            <p className="measurement-note">
              No sources verified yet. Verification checks local interface
              assignment without sending traffic to a destination.
            </p>
          )}
        </div>
        {requests.some((r) => ["queued", "unavailable"].includes(r.status)) && (
          <details className="measurement-requests">
            <summary>Source verification requests</summary>
            {requests
              .filter((r) => ["queued", "unavailable"].includes(r.status))
              .map((r) => (
                <p key={r.id}>
                  {r.source_address} · {label(r.status)} ·{" "}
                  {r.detail ?? "Waiting for the observation worker"}
                </p>
              ))}
          </details>
        )}
      </section>
      <div className="measurement-heading">
        <div>
          <h2>Checks and results</h2>
          <p>
            Paused checks send no new traffic. An in-flight observation may
            finish.
          </p>
        </div>
        <button
          className="primary"
          disabled={!accepted.length}
          onClick={() => open("policy")}
        >
          <Plus size={16} /> Create check
        </button>
      </div>
      {!rows.length && (
        <section className="panel empty">
          <Activity size={28} />
          <h3>
            {loaded
              ? "Build your first measured path"
              : "Loading measurements…"}
          </h3>
          <p>
            Start with reachability or packet loss, then add a latency
            distribution or service response. Each schedule is reviewed before
            it starts.
          </p>
        </section>
      )}
      <div className="measurement-grid">
        {rows.map((p) => {
          const r = p.latest_result,
            metrics = r?.metrics ?? {};
          const stale =
            r?.observed_at &&
            Date.now() - Date.parse(r.observed_at) >
              (p.parameters?.max_age_seconds ?? 120) * 1000;
          const status = !p.enabled
            ? "paused"
            : !r
              ? "awaiting_measurement"
              : stale
                ? "stale"
                : r.status;
          return (
            <article className="measurement-card" key={p.id}>
              <div className="measurement-card-top">
                <span className="eyebrow">
                  {p.check_id} · {titles[p.check_id] ?? "Measurement"}
                </span>
                <Badge value={status} />
              </div>
              <div className="measurement-path">
                <div>
                  <strong>{p.source.label}</strong>
                  <small>
                    {p.probe?.source_address ?? "Source not verified"}
                  </small>
                </div>
                <ArrowRight size={20} />
                <div>
                  <strong>{p.target.label}</strong>
                  <small>{p.target_address ?? "Choose an address"}</small>
                </div>
              </div>
              <div className="measurement-values">
                {p.check_id === "H02" ? (
                  <>
                    <Metric
                      title="95th percentile"
                      value={metrics.p95_rtt_ms}
                      unit="ms"
                    />
                    <Metric
                      title="Median RTT"
                      value={metrics.median_rtt_ms}
                      unit="ms"
                    />
                    <Metric title="Received samples" value={metrics.received} />
                  </>
                ) : p.check_id === "H04" ? (
                  <>
                    <Metric
                      title="Connection time"
                      value={metrics.connect_ms ?? metrics.duration_ms}
                      unit="ms"
                    />
                    <Metric title="HTTP status" value={metrics.http_status} />
                  </>
                ) : (
                  <>
                    <Metric
                      title="Packet loss"
                      value={metrics.loss_percent}
                      unit="%"
                    />
                    <Metric
                      title="Median RTT"
                      value={metrics.median_rtt_ms}
                      unit="ms"
                    />
                    <Metric title="Replies" value={metrics.received} />
                  </>
                )}
              </div>
              <p className="measurement-conclusion">
                {p.hold_reason ||
                  reasons[r?.reason] ||
                  r?.summary ||
                  "No measurement yet. Verify the source, then review this schedule."}
              </p>
              {p.check_id === "H02" && metrics.received < 100 && (
                <div className="measurement-progress">
                  <progress max={100} value={metrics.received ?? 0} />
                  <small>
                    {metrics.received ?? 0} of 100 replies needed for the first
                    distribution
                  </small>
                </div>
              )}
              <div className="measurement-meta">
                <span>
                  <Clock3 size={13} /> Every {p.interval_seconds} seconds
                </span>
                <span>Observed {time(r?.observed_at)}</span>
              </div>
              {metrics.window_start && (
                <p className="measurement-note">
                  Window: {time(metrics.window_start)} –{" "}
                  {time(metrics.window_end)}
                </p>
              )}
              {p.active_run &&
                ["queued", "running"].includes(p.active_run.status) && (
                  <p className="measurement-note">
                    {p.active_run.status === "queued"
                      ? "Waiting for the observation worker"
                      : "Measurement in progress"}
                  </p>
                )}
              <div className="measurement-actions">
                <button disabled={busy} onClick={() => open("policy", p)}>
                  <Settings2 size={14} /> Edit
                </button>
                {p.enabled ? (
                  <>
                    <button
                      disabled={busy}
                      onClick={() =>
                        act(
                          () => api(`/check-policies/${p.id}/run`, "POST", {}),
                          false,
                        )
                      }
                    >
                      <Play size={14} /> Run now
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        act(
                          () =>
                            api(
                              `/check-policies/${p.id}/pause`,
                              "POST",
                              {},
                              p.revision,
                            ),
                          false,
                        )
                      }
                    >
                      <Pause size={14} /> Pause
                    </button>
                  </>
                ) : (
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        const review = await api(
                          `/check-policies/${p.id}/review`,
                          "POST",
                          {},
                        );
                        open("review", review);
                      }, false)
                    }
                  >
                    Review and start <ArrowRight size={14} />
                  </button>
                )}
                <button
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      const result = await api(
                        `/check-policies/${p.id}/results`,
                      );
                      setHistory(result.items);
                      open("history", p);
                    }, false)
                  }
                >
                  Results
                </button>
              </div>
            </article>
          );
        })}
      </div>
      {(cursor || next) && (
        <div className="toolbar-actions">
          {cursor && <button onClick={() => setCursor("")}>First page</button>}
          {next && <button onClick={() => setCursor(next)}>Next checks</button>}
        </div>
      )}
      <Dialog.Root
        open={!!mode}
        onOpenChange={(value) => {
          if (!value && !busy) {
            setMode("");
            setError("");
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="dialog measurement-dialog">
            <header>
              <div>
                <Dialog.Title>
                  {mode === "probe"
                    ? "Verify a measurement source"
                    : mode === "review"
                      ? "Review this measurement schedule"
                      : mode === "history"
                        ? "Measurement history"
                        : selected
                          ? "Edit measurement policy"
                          : "Create a measurement policy"}
                </Dialog.Title>
                <Dialog.Description>
                  {mode === "history"
                    ? "The latest 60 results, with their actual observation times."
                    : "Define the path and measurement budget before enabling collection."}
                </Dialog.Description>
              </div>
              <Dialog.Close aria-label="Close" disabled={busy}>
                <X size={18} />
              </Dialog.Close>
            </header>
            {error && (
              <div className="error" role="alert">
                {error}
              </div>
            )}
            {mode === "probe" && (
              <ProbeForm devices={accepted} busy={busy} act={act} />
            )}
            {mode === "policy" && (
              <PolicyForm
                key={selected?.id ?? "new"}
                devices={accepted}
                policy={selected}
                busy={busy}
                act={act}
              />
            )}
            {mode === "review" && selected && (
              <ReviewForm
                review={selected}
                devices={devices}
                busy={busy}
                act={act}
              />
            )}
            {mode === "history" && (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Observed</th>
                      <th>Result</th>
                      <th>Finding</th>
                      <th>Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((r) => (
                      <tr key={r.id}>
                        <td>{time(r.observed_at)}</td>
                        <td>
                          <Badge value={r.status} />
                        </td>
                        <td>{reasons[r.reason] ?? r.summary}</td>
                        <td>{r.evidence_ids?.length ?? 0} records</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!history.length && (
                  <p className="measurement-note">
                    No results have been collected for this policy.
                  </p>
                )}
              </div>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

type Action = (
  operation: () => Promise<unknown>,
  close?: boolean,
) => Promise<void>;
function ProbeForm({
  devices,
  busy,
  act,
}: {
  devices: RecordData[];
  busy: boolean;
  act: Action;
}) {
  const [source, setSource] = useState(devices[0]?.id ?? "");
  const chosen = devices.find((d) => d.id === source);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const data = new FormData(e.currentTarget);
        act(() =>
          api("/probe-requests", "POST", {
            source_id: source,
            source_address: data.get("address"),
          }),
        );
      }}
    >
      <label>
        Accepted source identity
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          required
        >
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Source address
        <select name="address" key={source} required>
          {chosen?.addresses.map((a: string) => (
            <option key={a}>{a}</option>
          ))}
        </select>
      </label>
      <p className="notice">
        Choose the identity of the observation worker. Its selected address must
        be assigned to an active local interface. A switch address cannot act as
        a local probe.
      </p>
      <div className="dialog-actions">
        <button
          className="primary"
          disabled={busy || !chosen?.addresses.length}
        >
          Verify source
        </button>
      </div>
    </form>
  );
}
function PolicyForm({
  devices,
  policy,
  busy,
  act,
}: {
  devices: RecordData[];
  policy: RecordData | null;
  busy: boolean;
  act: Action;
}) {
  const [check, setCheck] = useState(policy?.check_id ?? "H01"),
    [target, setTarget] = useState(policy?.target_id ?? devices[0]?.id ?? "");
  const [protocol, setProtocol] = useState(
    policy?.parameters?.protocol ?? "https",
  );
  const parameters = policy?.parameters ?? {};
  const destinations = devices.find((d) => d.id === target)?.addresses ?? [];
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        const values: Record<string, unknown> = {
          max_age_seconds: Number(f.get("max_age")),
        };
        if (check !== "H04") values.samples = Number(f.get("samples"));
        if (["H01", "H03"].includes(check))
          values.max_loss_percent = Number(f.get("loss"));
        if (check === "H02") {
          values.max_rtt_ms = Number(f.get("rtt"));
          values.window_seconds = Number(f.get("window"));
        }
        if (check === "H04")
          Object.assign(values, {
            protocol,
            port:
              protocol === "tcp"
                ? Number(f.get("port"))
                : { ssh: 22, http: 80, https: 443 }[protocol as string],
            ...(protocol === "http" || protocol === "https"
              ? {
                  hostname: f.get("hostname"),
                  path: f.get("path"),
                  expected_status: Number(f.get("status")),
                }
              : {}),
          });
        act(() =>
          api(
            policy ? `/check-policies/${policy.id}` : "/check-policies",
            policy ? "PATCH" : "POST",
            {
              check_id: check,
              source_id: f.get("source"),
              target_id: target,
              target_address: f.get("address"),
              interval_seconds: Number(f.get("interval")),
              enabled: false,
              parameters: values,
            },
            policy?.revision,
          ),
        );
      }}
    >
      <label>
        Check
        <select value={check} onChange={(e) => setCheck(e.target.value)}>
          {Object.entries(titles).map(([id, title]) => (
            <option key={id} value={id}>
              {title}
            </option>
          ))}
        </select>
      </label>
      <div className="measurement-form-grid">
        <label>
          Measurement source
          <select
            name="source"
            defaultValue={policy?.source_id ?? devices[0]?.id}
            required
          >
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Destination
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            required
          >
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Destination address
        <select
          name="address"
          key={target}
          defaultValue={policy?.target_address}
          required
        >
          {destinations.map((a: string) => (
            <option key={a}>{a}</option>
          ))}
        </select>
      </label>
      <div className="measurement-form-grid">
        <label>
          Interval · seconds
          <input
            name="interval"
            type="number"
            min={60}
            max={604800}
            defaultValue={policy?.interval_seconds ?? 300}
            required
          />
        </label>
        <label>
          Result freshness · seconds
          <input
            name="max_age"
            type="number"
            min={30}
            max={900}
            defaultValue={parameters.max_age_seconds ?? 900}
            required
          />
        </label>
      </div>
      {check !== "H04" && (
        <label>
          Packets per run
          <input
            name="samples"
            type="number"
            min={1}
            max={10}
            defaultValue={parameters.samples ?? 10}
            required
          />
        </label>
      )}
      {["H01", "H03"].includes(check) && (
        <label>
          Maximum accepted loss · %
          <input
            name="loss"
            type="number"
            min={0}
            max={100}
            step="any"
            defaultValue={parameters.max_loss_percent ?? ""}
            placeholder="Choose the accepted limit"
            required
          />
        </label>
      )}
      {check === "H02" && (
        <div className="measurement-form-grid">
          <label>
            Maximum 95th percentile RTT · ms
            <input
              name="rtt"
              type="number"
              min={0.001}
              max={60000}
              step="any"
              defaultValue={parameters.max_rtt_ms ?? ""}
              required
            />
          </label>
          <label>
            Rolling window · seconds
            <input
              name="window"
              type="number"
              min={600}
              max={86400}
              defaultValue={parameters.window_seconds ?? 3600}
              required
            />
          </label>
        </div>
      )}
      {check === "H04" && (
        <>
          <label>
            Response to verify
            <select
              value={protocol}
              onChange={(e) => setProtocol(e.target.value)}
            >
              <option value="https">HTTPS status and certificate</option>
              <option value="http">HTTP status</option>
              <option value="ssh">SSH version 2 banner</option>
              <option value="tcp">TCP connection only</option>
            </select>
          </label>
          {protocol === "tcp" && (
            <>
              <label>
                Port
                <select name="port" defaultValue={parameters.port ?? 443}>
                  {[22, 53, 80, 443].map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </label>
              <p className="notice">
                A successful TCP connection leaves application health unknown.
                Choose an application response when one is available.
              </p>
            </>
          )}
          {["http", "https"].includes(protocol) && (
            <>
              <label>
                Expected hostname
                <input
                  name="hostname"
                  defaultValue={parameters.hostname ?? ""}
                  placeholder="monitoring.example.org"
                  required
                />
              </label>
              <div className="measurement-form-grid">
                <label>
                  Read-only health path
                  <input
                    name="path"
                    defaultValue={parameters.path ?? "/"}
                    required
                  />
                </label>
                <label>
                  Expected HTTP status
                  <input
                    name="status"
                    type="number"
                    min={100}
                    max={599}
                    defaultValue={parameters.expected_status ?? 200}
                    required
                  />
                </label>
              </div>
              <p className="notice">
                Use a read-only endpoint. The connection goes to the selected
                address; redirects are not followed.
              </p>
            </>
          )}
        </>
      )}
      <p className="notice">
        Saving creates a paused draft. Review its source, destination, limits
        and traffic budget to start collection.
      </p>
      <div className="dialog-actions">
        <button className="primary" disabled={busy || !devices.length}>
          Save paused policy
        </button>
      </div>
    </form>
  );
}
function ReviewForm({
  review,
  devices,
  busy,
  act,
}: {
  review: RecordData;
  devices: RecordData[];
  busy: boolean;
  act: Action;
}) {
  const { policy, context, budget } = review.content;
  const name = (id: string) => devices.find((d) => d.id === id)?.label ?? id;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const password = new FormData(e.currentTarget).get("password");
        act(async () => {
          await api("/session/reauthenticate", "POST", { password });
          await api(`/check-reviews/${review.id}/approve`, "POST", {
            digest: review.digest,
            revision: review.revision,
          });
        });
      }}
    >
      <div className="measurement-review">
        <h3>{titles[policy.check_id]}</h3>
        <dl>
          <dt>From</dt>
          <dd>
            {name(policy.source_id)} · {context.probe.source_address}
          </dd>
          <dt>To</dt>
          <dd>
            {name(policy.target_id)} · {policy.target_address}
          </dd>
          <dt>Traffic budget</dt>
          <dd>
            {budget.packets_per_run
              ? `${budget.packets_per_run} ICMP packets`
              : `1 ${policy.parameters.protocol.toUpperCase()} connection`}{" "}
            every {policy.interval_seconds} seconds
          </dd>
          <dt>Observation limit</dt>
          <dd>At most {budget.max_duration_seconds} seconds per run</dd>
          <dt>Accepted limit</dt>
          <dd>
            {policy.check_id === "H02"
              ? `95th percentile RTT ≤ ${policy.parameters.max_rtt_ms} ms, across ${policy.parameters.window_seconds} seconds`
              : policy.check_id === "H04"
                ? policy.parameters.protocol === "tcp"
                  ? "Transport connection only; application remains untested"
                  : policy.parameters.protocol === "ssh"
                    ? "SSH version 2 protocol banner"
                    : `HTTP ${policy.parameters.expected_status} from ${policy.parameters.hostname}${policy.parameters.path}`
                : `Packet loss ≤ ${policy.parameters.max_loss_percent}%`}
          </dd>
          <dt>Scope</dt>
          <dd>
            Controller-origin traffic from {context.probe.binding.interface}
          </dd>
          <dt>Review expires</dt>
          <dd>{time(review.expires_at)}</dd>
        </dl>
      </div>
      <p className="notice">
        This approval starts the recurring schedule. Three independent failing
        windows open an incident; three passing windows resolve it. Overlapping
        latency windows count once.
      </p>
      <label>
        Confirm your application password
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </label>
      <div className="dialog-actions">
        <button className="primary" disabled={busy}>
          Approve and start schedule
        </button>
      </div>
    </form>
  );
}
