import React, { useCallback, useEffect, useState } from "react";
import { ArrowRight, BrainCircuit, RefreshCw, ShieldCheck } from "lucide-react";
import { api, label, RecordData, time } from "./api";

function Message({ error }: { error: string }) {
  return error ? (
    <p className="error" role="alert">
      {error}
    </p>
  ) : null;
}

export function AIRoutes({ connectors }: { connectors: RecordData[] }) {
  const [routes, setRoutes] = useState<RecordData[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<RecordData | null>(null);
  const refresh = useCallback(
    async () => setRoutes((await api("/ai-routes")).items),
    [],
  );
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, [refresh]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel ai-routes">
      <div className="panel-heading">
        <h2>
          <BrainCircuit size={20} /> AI provider routes
        </h2>
        <span className="count-pill">
          {routes.filter((r) => r.eligible).length} eligible
        </span>
      </div>
      <p className="muted">
        Choose the free providers this installation may use, in rollover order.
        Each diagnosis shows its evidence packet before anything is sent.
      </p>
      <Message error={error} />
      <div className="ai-route-list">
        {routes.map((route) => (
          <article key={route.id}>
            <div>
              <strong>{route.content.name}</strong>
              <small>
                {route.content.model} · priority {route.content.priority}
              </small>
              <small>Review expires {time(route.content.expires_at)}</small>
            </div>
            <span
              className={"badge " + (route.eligible ? "passed" : "unknown")}
            >
              {route.eligible
                ? "Eligible"
                : label(route.health?.reason ?? route.status)}
            </span>
            <button
              disabled={busy}
              onClick={() =>
                setSelected(selected?.id === route.id ? null : route)
              }
            >
              Review
            </button>
            {route.status === "approved" && (
              <button
                disabled={busy}
                onClick={() =>
                  act(() =>
                    api(
                      `/ai-routes/${route.id}/pause`,
                      "POST",
                      {},
                      route.revision,
                    ),
                  )
                }
              >
                Pause
              </button>
            )}
          </article>
        ))}
      </div>
      {selected && (
        <div className="ai-review">
          <h3>{selected.content.name}</h3>
          <p>{selected.content.qualification_note}</p>
          <dl className="readiness">
            <div>
              <dt>Expected provider / model</dt>
              <dd>
                {selected.content.provider} / {selected.content.response_model}
              </dd>
            </div>
            <div>
              <dt>Eligibility reference</dt>
              <dd>
                <a
                  href={selected.content.free_terms_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Provider terms
                </a>
              </dd>
            </div>
          </dl>
          {selected.status === "draft" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const password = String(
                  new FormData(e.currentTarget).get("password"),
                );
                act(async () => {
                  await api("/session/reauthenticate", "POST", { password });
                  await api(`/ai-routes/${selected.id}/approve`, "POST", {
                    digest: selected.digest,
                    revision: selected.revision,
                  });
                  setSelected(null);
                });
              }}
            >
              <label className="field">
                <span>Application password to approve this route</span>
                <input
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  required
                />
              </label>
              <button className="primary" disabled={busy}>
                <ShieldCheck size={16} /> Approve route
              </button>
            </form>
          )}
        </div>
      )}
      <details>
        <summary>Add a provider route</summary>
        <p>
          The gateway must have a dedicated free-only connection set, with paid
          billing, aliases and gateway fallbacks disabled. A zero-cost response
          header alone does not establish this. Record how you verified the
          gateway and provider account below.
        </p>
        <form
          className="ai-route-form"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            const form = e.currentTarget;
            act(async () => {
              const draft = await api("/ai-routes", "POST", {
                name: f.get("name"),
                connector_id: f.get("connector"),
                model: f.get("model"),
                response_model: f.get("response_model"),
                provider: f.get("provider"),
                free_terms_url: f.get("terms"),
                qualification_note: f.get("note"),
                expires_at: new Date(String(f.get("expires"))).toISOString(),
                priority: Number(f.get("priority")),
                free_only_gateway_verified: f.get("verified") === "on",
              });
              setSelected(draft);
              form.reset();
              const details = form.closest("details");
              if (details) details.open = false;
            });
          }}
        >
          <label className="field">
            <span>Name</span>
            <input name="name" required maxLength={80} />
          </label>
          <label className="field">
            <span>OmniRoute connection</span>
            <select name="connector" required defaultValue="">
              <option value="" disabled>
                Choose a connection
              </option>
              {connectors
                .filter((c) => c.kind === "omniroute")
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            <span>Exact request model</span>
            <input name="model" placeholder="provider/model" required />
          </label>
          <label className="field">
            <span>Expected response model</span>
            <input name="response_model" required />
          </label>
          <label className="field">
            <span>Expected provider identifier</span>
            <input name="provider" required />
          </label>
          <label className="field">
            <span>Priority (lower first)</span>
            <input
              name="priority"
              type="number"
              min={1}
              max={100}
              defaultValue={10}
              required
            />
          </label>
          <label className="field">
            <span>Provider free-tier terms</span>
            <input name="terms" type="url" placeholder="https://" required />
          </label>
          <label className="field">
            <span>Review expiry (within 30 days)</span>
            <input name="expires" type="datetime-local" required />
          </label>
          <label className="field wide">
            <span>How free-only routing and billing were verified</span>
            <textarea name="note" minLength={20} maxLength={1000} required />
          </label>
          <label className="wide checkbox-line">
            <input type="checkbox" name="verified" required /> I verified the
            dedicated gateway configuration and the provider account’s free-only
            limits.
          </label>
          <div className="wide">
            <button
              disabled={busy || !connectors.some((c) => c.kind === "omniroute")}
            >
              Prepare route review <ArrowRight size={16} />
            </button>
          </div>
        </form>
      </details>
    </section>
  );
}

export function DiagnosisPanel({
  kind,
  subjectId,
}: {
  kind: "incident" | "recommendation";
  subjectId: string;
}) {
  const [items, setItems] = useState<RecordData[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(
    async () =>
      setItems((await api(`/diagnosis-history/${kind}/${subjectId}`)).items),
    [kind, subjectId],
  );
  useEffect(() => {
    setItems([]);
    refresh().catch((e) => setError(e.message));
  }, [refresh]);
  const active = items.some((d) => ["queued", "running"].includes(d.status));
  const hasHistory = items.length > 0;
  useEffect(() => {
    if (!hasHistory) return;
    const timer = setInterval(
      () => refresh().catch((e) => setError(e.message)),
      active ? 3000 : 30000,
    );
    return () => clearInterval(timer);
  }, [active, hasHistory, refresh]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const latest = items[0];
  return (
    <section className="diagnosis-panel">
      <div className="panel-heading">
        <h3>
          <BrainCircuit size={19} /> Evidence-based diagnosis
        </h3>
        <button
          disabled={busy || active}
          onClick={() =>
            act(() =>
              api("/diagnosis-previews", "POST", { kind, id: subjectId }),
            )
          }
        >
          Prepare diagnosis
        </button>
      </div>
      <p className="muted">
        Review the observations sent to your approved providers. Explanations
        are hypotheses; device changes require a separate maintenance plan.
      </p>
      <Message error={error} />
      {latest && (
        <>
          <div className="detail-status">
            <span className={"badge " + latest.status}>
              {label(latest.status)}
            </span>
            <span>{time(latest.created_at)}</span>
            {!latest.current && <strong>Evidence has changed</strong>}
          </div>
          {latest.reason && <p>{latest.reason}</p>}
          <details open={latest.status === "preview"}>
            <summary>
              Evidence packet · {latest.packet.observations.length} observations
            </summary>
            <div className="evidence-cards">
              {latest.packet.observations.map((o: any) => (
                <article key={o.citation} id={o.citation}>
                  <strong>
                    {o.check_id ?? "Configuration observations"} ·{" "}
                    {label(o.status ?? "recorded")}
                  </strong>
                  <small>
                    {time(o.observed_at)} ·{" "}
                    {o.fresh ? "Current at preparation" : "Historical"}
                  </small>
                  <dl>
                    {Object.entries(o.metrics ?? o.facts ?? {}).map(
                      ([key, value]) => (
                        <div key={key}>
                          <dt>{label(key)}</dt>
                          <dd>{String(value ?? "Unmeasured")}</dd>
                        </div>
                      ),
                    )}
                  </dl>
                  <code>{o.citation}</code>
                </article>
              ))}
            </div>
            <p>
              {latest.packet.devices
                .map((d: any) => d.label ?? d.id)
                .join(" → ")}
            </p>
            <ul>
              {latest.packet.gaps.map((gap: string) => (
                <li key={gap}>{gap}</li>
              ))}
            </ul>
            <details>
              <summary>Inspect the exact outgoing packet</summary>
              <pre>{JSON.stringify(latest.packet, null, 2)}</pre>
            </details>
          </details>
          {latest.status === "preview" && (
            <form
              className="ai-review"
              onSubmit={(e) => {
                e.preventDefault();
                const password = String(
                  new FormData(e.currentTarget).get("password"),
                );
                act(async () => {
                  await api("/session/reauthenticate", "POST", { password });
                  await api(`/diagnoses/${latest.id}/start`, "POST", {
                    digest: latest.digest,
                    revision: latest.revision,
                  });
                });
              }}
            >
              <h4>Provider order</h4>
              <ol>
                {latest.routes.map((r: any) => (
                  <li key={r.id}>
                    {r.name} · {r.model}
                  </li>
                ))}
              </ol>
              <p>
                At most {latest.limits.maximum_calls} calls,{" "}
                {latest.limits.seconds_per_call} seconds and{" "}
                {latest.limits.output_tokens_per_call} output tokens per call.
                Preview expires {time(latest.expires_at)}.
              </p>
              <label className="field">
                <span>Application password</span>
                <input
                  name="password"
                  type="password"
                  required
                  autoComplete="current-password"
                />
              </label>
              <button className="primary" disabled={busy || !latest.current}>
                Send reviewed evidence <ArrowRight size={16} />
              </button>
            </form>
          )}
          {active && (
            <div className="dialog-actions">
              <span>
                <RefreshCw size={15} /> Waiting for a bounded provider response
              </span>
              <button
                disabled={busy}
                onClick={() =>
                  act(() => api(`/diagnoses/${latest.id}/cancel`, "POST", {}))
                }
              >
                Cancel diagnosis
              </button>
            </div>
          )}
          {latest.output && (
            <div className="diagnosis-output">
              <h4>
                {latest.current
                  ? "Model assessment"
                  : "Historical assessment — refresh the evidence before using it"}
              </h4>
              <p>{latest.output.summary}</p>
              {[
                ["Possible explanations", latest.output.hypotheses],
                ["Alternatives", latest.output.alternatives],
              ].map(
                ([heading, claims]) =>
                  (claims as any[]).length > 0 && (
                    <div key={String(heading)}>
                      <h4>{String(heading)}</h4>
                      {(claims as any[]).map((claim, i) => (
                        <article key={i}>
                          <span className="eyebrow">
                            {claim.confidence} model confidence
                          </span>
                          <p>{claim.explanation}</p>
                          <Citations values={claim.citations} />
                        </article>
                      ))}
                    </div>
                  ),
              )}
              {latest.output.missing_information.length > 0 && (
                <>
                  <h4>What would narrow this down</h4>
                  <ul>
                    {latest.output.missing_information.map(
                      (value: string, i: number) => (
                        <li key={i}>{value}</li>
                      ),
                    )}
                  </ul>
                </>
              )}
              {latest.output.next_steps.length > 0 && (
                <>
                  <h4>Suggested next steps</h4>
                  {latest.output.next_steps.map((step: any, i: number) => (
                    <article key={i}>
                      <span className="eyebrow">
                        {label(step.kind)}
                        {step.runbook_id
                          ? ` · ${step.runbook_id} candidate`
                          : ""}
                      </span>
                      <p>{step.description}</p>
                      <small>
                        {latest.packet.devices.find(
                          (d: any) => d.id === step.device_id,
                        )?.label ?? step.device_id}
                      </small>
                      <Citations values={step.citations} />
                    </article>
                  ))}
                </>
              )}
            </div>
          )}
          {latest.attempts.length > 0 && (
            <details>
              <summary>Provider attempts</summary>
              <ol>
                {latest.attempts.map((a: any) => (
                  <li key={a.id}>
                    {latest.routes.find((r: any) => r.id === a.route_id)?.name}{" "}
                    · {label(a.reason ?? a.status)} · {time(a.started_at)}
                  </li>
                ))}
              </ol>
            </details>
          )}
          {items.length > 1 && (
            <details>
              <summary>Earlier requests ({items.length - 1})</summary>
              {items.slice(1).map((item) => (
                <p key={item.id}>
                  {time(item.created_at)} · {label(item.status)}
                  {item.output ? ` · ${item.output.summary}` : ""}
                </p>
              ))}
            </details>
          )}
        </>
      )}
    </section>
  );
}
function Citations({ values }: { values: string[] }) {
  return (
    <div className="ai-citations">
      {values.map((c) => (
        <a
          key={c}
          href={`#${c}`}
          onClick={(event) => {
            event.preventDefault();
            const node = document.getElementById(c);
            const details = node?.closest("details");
            if (details) details.open = true;
            node?.scrollIntoView({ block: "center" });
          }}
        >
          {c}
        </a>
      ))}
    </div>
  );
}
