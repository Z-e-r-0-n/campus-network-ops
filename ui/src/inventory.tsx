import { useEffect, useState } from "react";
import { CheckCheck, Plus, RefreshCw } from "lucide-react";
import { api, label, time, type RecordData } from "./api";

type Action = (
  operation: () => Promise<unknown>,
  close?: boolean,
) => Promise<void>;
type Open = (mode: string, record?: RecordData) => void;
type Props = {
  devices: RecordData[];
  edges: RecordData[];
  connectors: RecordData[];
  choices: RecordData[];
};

export function ExcludedDevices({
  open,
  refreshKey,
}: {
  open: Open;
  refreshKey: string;
}) {
  const [items, setItems] = useState<RecordData[]>([]),
    [cursor, setCursor] = useState("");
  const [next, setNext] = useState<string | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    api(`/excluded-devices?after=${encodeURIComponent(cursor)}`)
      .then((data) => {
        if (current) {
          setItems(data.items);
          setNext(data.next_cursor);
          setError("");
        }
      })
      .catch((e) => {
        if (current) setError(e.message);
      });
    return () => {
      current = false;
    };
  }, [cursor, refreshKey]);
  return (
    <details className="inventory-requests">
      <summary>Excluded identities</summary>
      {error && <p role="alert">{error}</p>}
      <p>
        Excluded identities remain in history. Discovery skips their known
        addresses and does not restore them automatically.
      </p>
      {items.length ? (
        <div className="activity-list">
          {items.map((item) => (
            <button
              key={item.id}
              onClick={() => open("identity-restore", item)}
            >
              <div>
                <strong>{item.label}</strong>
                <small>
                  {item.addresses.join(", ")} · {item.reason}
                </small>
              </div>
              <span>Restore to draft</span>
            </button>
          ))}
        </div>
      ) : (
        <p>No excluded identities on this page.</p>
      )}
      <div className="toolbar-actions">
        {cursor && <button onClick={() => setCursor("")}>First page</button>}
        {next && (
          <button onClick={() => setCursor(next)}>Next identities</button>
        )}
      </div>
    </details>
  );
}

export function InventoryPanel({
  baselines,
  connectors,
  requests,
  busy,
  act,
  open,
}: {
  baselines: RecordData[];
  connectors: RecordData[];
  requests: RecordData[];
  busy: boolean;
  act: Action;
  open: Open;
}) {
  const inventories = connectors.filter((c) => c.kind === "netbox");
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>Review intended inventory</h2>
          <p>
            Choose the devices and connections you accept. Collection alone does
            not approve them.
          </p>
        </div>
        <div className="toolbar-actions">
          <button
            disabled={!inventories.length}
            onClick={() => open("inventory-object")}
          >
            <Plus size={16} /> Add inventory details
          </button>
          <button
            className="primary"
            disabled={!inventories.length}
            onClick={() => open("baseline-prepare")}
          >
            <CheckCheck size={16} /> Prepare review
          </button>
        </div>
      </div>
      <div className="panel-foot">
        {inventories.length
          ? inventories.map((c) => (
              <button
                key={c.id}
                disabled={busy}
                onClick={() =>
                  act(
                    () => api(`/connectors/${c.id}/inventory-options`, "POST"),
                    false,
                  )
                }
              >
                <RefreshCw size={14} /> Refresh {c.name} choices
              </button>
            ))
          : "Connect intended inventory from Settings to accept reviewed devices."}
      </div>
      {baselines.length ? (
        <div className="activity-list">
          {baselines.map((b) => (
            <button key={b.id} onClick={() => open("baseline-review", b)}>
              <div>
                <strong>
                  {b.devices.length} devices · {b.edges.length} connections
                </strong>
                <small>
                  {b.reason} · {time(b.created_at)}
                </small>
              </div>
              <span className="badge">{label(b.status)}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="empty">
          <CheckCheck size={26} />
          <h3>No inventory accepted yet</h3>
          <p>
            Correct device names, roles and connections first. Review the exact
            proposal before publishing it to your intended inventory.
          </p>
        </div>
      )}
      {requests.length > 0 && (
        <details className="inventory-requests">
          <summary>Recent inventory requests</summary>
          {requests.slice(-10).map((r) => (
            <p key={r.id}>
              <strong>{r.name ?? "Refresh choices"}</strong> · {label(r.status)}{" "}
              {r.detail && <small>{r.detail}</small>}
            </p>
          ))}
        </details>
      )}
    </section>
  );
}

export function InventoryForm({
  mode,
  selected,
  devices,
  edges,
  connectors,
  choices,
  busy,
  act,
}: Props & {
  mode: string;
  selected: RecordData | null;
  busy: boolean;
  act: Action;
}) {
  const inventories = connectors.filter((c) => c.kind === "netbox");
  const [connector, setConnector] = useState(inventories[0]?.id ?? "");
  const [kind, setKind] = useState("sites");
  const options =
    choices.find((c) => c.connector_id === connector)?.options ?? {};
  const picker = (
    <label className="field">
      Intended inventory
      <select
        name="connector_id"
        required
        value={connector}
        onChange={(e) => setConnector(e.target.value)}
      >
        <option value="" disabled>
          Choose a connection
        </option>
        {inventories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
    </label>
  );
  if (mode === "inventory-object")
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          act(() =>
            api(`/connectors/${connector}/inventory-objects`, "POST", {
              kind,
              name: f.get("name"),
              manufacturer_id:
                kind === "device-types" ? Number(f.get("manufacturer")) : null,
              reason: f.get("reason"),
            }),
          );
        }}
      >
        {picker}
        <label className="field">
          Detail to add
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="sites">Site</option>
            <option value="manufacturers">Manufacturer</option>
            <option value="device-types">Device model</option>
            <option value="device-roles">Inventory role</option>
          </select>
        </label>
        <label className="field">
          Name
          <input name="name" required maxLength={100} />
        </label>
        {kind === "device-types" && (
          <label className="field">
            Manufacturer
            <select name="manufacturer" required defaultValue="">
              <option value="" disabled>
                Choose a manufacturer
              </option>
              {(options.manufacturers ?? []).map((o: any) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          Reason
          <textarea name="reason" required minLength={3} maxLength={500} />
        </label>
        <p className="notice">
          This adds an inventory choice. It does not create a discovered device
          or change the network.
        </p>
        <div className="dialog-actions">
          <button className="primary" disabled={busy || !connector}>
            Add detail
          </button>
        </div>
      </form>
    );
  if (mode === "baseline-prepare") {
    const eligible = devices.filter(
      (d) =>
        !["conflict", "merged", "excluded"].includes(d.status) &&
        d.role !== "unknown",
    );
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          const picked = f.getAll("device").map(String);
          const chosenEdges = f.getAll("edge").map(String);
          act(() =>
            api("/baseline-drafts", "POST", {
              connector_id: connector,
              reason: f.get("reason"),
              devices: eligible
                .filter((d) => picked.includes(d.id))
                .map((d) => ({
                  id: d.id,
                  revision: d.revision,
                  mapping: {
                    site: Number(f.get(`site-${d.id}`)),
                    device_type: Number(f.get(`type-${d.id}`)),
                    role: Number(f.get(`role-${d.id}`)),
                    status: f.get(`status-${d.id}`),
                    ...(d.netbox
                      ? {
                          external_id: d.netbox.external_id,
                          expected_last_updated: d.netbox.last_updated,
                          expected_etag: d.netbox.etag,
                        }
                      : {}),
                  },
                })),
              edges: edges
                .filter((edge) => chosenEdges.includes(edge.id))
                .map((edge) => ({ id: edge.id, revision: edge.revision })),
            }),
          );
        }}
      >
        {picker}
        <p className="notice">
          Only devices with a reviewed role and no identity conflict are
          available. Review a device from the Devices tab to correct it.
        </p>
        {eligible.map((d) => (
          <fieldset className="inventory-device" key={d.id}>
            <legend>{d.label}</legend>
            <label className="checkbox-field">
              <input name="device" type="checkbox" value={d.id} />
              <span>
                Include this device
                <small>
                  {d.addresses.join(", ")} · {label(d.role)}
                </small>
              </span>
            </label>
            <div className="inventory-mapping">
              {[
                ["sites", "site", "Site"],
                ["device-types", "type", "Model"],
                ["device-roles", "role", "Inventory role"],
              ].map(([list, name, title]) => (
                <label className="field" key={name}>
                  {title}
                  <select name={`${name}-${d.id}`} defaultValue="">
                    <option value="">Choose {title.toLowerCase()}</option>
                    {(options[list] ?? []).map((o: any) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <label className="field">
                Deployment status
                <select name={`status-${d.id}`} defaultValue="">
                  <option value="">Choose status</option>
                  {[
                    "active",
                    "offline",
                    "planned",
                    "staged",
                    "inventory",
                    "decommissioning",
                  ].map((s) => (
                    <option key={s} value={s}>
                      {label(s)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </fieldset>
        ))}
        {!eligible.length && <p>No devices are ready for this review.</p>}
        {edges
          .filter((edge) => edge.decision === "include")
          .map((edge) => (
            <label className="checkbox-field" key={edge.id}>
              <input name="edge" type="checkbox" value={edge.id} />
              <span>
                {devices.find((d) => d.id === edge.source_id)?.label} →{" "}
                {devices.find((d) => d.id === edge.target_id)?.label}
                <small>
                  {label(edge.layer)} · {edge.local_port} / {edge.remote_port}
                </small>
              </span>
            </label>
          ))}
        <label className="field">
          Review note
          <textarea name="reason" required minLength={3} maxLength={500} />
        </label>
        <div className="dialog-actions">
          <button
            className="primary"
            disabled={busy || !eligible.length || !connector}
          >
            Prepare exact review
          </button>
        </div>
      </form>
    );
  }
  if (mode === "baseline-review" && selected) {
    const named = (id: number, list: string) =>
      choices
        .find((c) => c.connector_id === selected.connector_id)
        ?.options?.[list]?.find((o: any) => o.id === id)?.name ??
      `Record ${id}`;
    return (
      <>
        <p>
          <span className="badge">{label(selected.status)}</span>{" "}
          {time(selected.created_at)}
        </p>
        <p>{selected.reason}</p>
        <div className="baseline-summary">
          {selected.devices.map((d: any) => (
            <article key={d.id}>
              <h3>{d.reviewed.label}</h3>
              <p>
                {d.reviewed.addresses.join(", ")} · {label(d.reviewed.role)}
              </p>
              <dl>
                <dt>Site</dt>
                <dd>{named(d.mapping.site, "sites")}</dd>
                <dt>Model</dt>
                <dd>{named(d.mapping.device_type, "device-types")}</dd>
                <dt>Inventory role</dt>
                <dd>{named(d.mapping.role, "device-roles")}</dd>
                <dt>Deployment</dt>
                <dd>{label(d.mapping.status)}</dd>
              </dl>
            </article>
          ))}
        </div>
        {selected.edges.map((e: any) => (
          <p key={e.id}>
            <strong>
              {selected.devices.find((d: any) => d.id === e.reviewed.source_id)
                ?.reviewed.label ?? e.reviewed.source_id}
              {" → "}
              {selected.devices.find((d: any) => d.id === e.reviewed.target_id)
                ?.reviewed.label ?? e.reviewed.target_id}
            </strong>
            <br />
            {label(e.reviewed.layer)} ·{" "}
            {e.reviewed.local_port || "Unknown port"} /{" "}
            {e.reviewed.remote_port || "Unknown port"}
          </p>
        ))}
        {selected.detail && <p className="notice">{selected.detail}</p>}
        <p className="notice">
          Approval accepts exactly these device and connection revisions.
          Changed records stop publication. Partial external writes remain
          visible for reconciliation.
        </p>
        <details>
          <summary>Review identifier and revisions</summary>
          <code className="fingerprint">{selected.digest}</code>
          <p>Review revision {selected.revision}</p>
        </details>
        {["awaiting_review", "publication_failed"].includes(
          selected.status,
        ) && (
          <div className="dialog-actions">
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                act(() =>
                  api(`/baseline-drafts/${selected.id}/approve`, "POST", {
                    digest: selected.digest,
                    revision: selected.revision,
                  }),
                )
              }
            >
              Approve and publish inventory
            </button>
          </div>
        )}
      </>
    );
  }
  if (mode === "identity-merge" && selected)
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          const other = devices.find((d) => d.id === f.get("alias"));
          act(() =>
            api("/identity-merges", "POST", {
              primary: { id: selected.id, revision: selected.revision },
              aliases: other
                ? [{ id: other.id, revision: other.revision }]
                : [],
              reason: f.get("reason"),
            }),
          );
        }}
      >
        <p>
          Keep <strong>{selected.label}</strong> and combine a verified alias
          with it. Source evidence remains in history.
        </p>
        <label className="field">
          Alias record
          <select name="alias" required defaultValue="">
            <option value="" disabled>
              Choose the duplicate record
            </option>
            {devices
              .filter((d) => d.id !== selected.id && !d.netbox)
              .map((d) => (
                <option key={d.id} value={d.id}>
                  {d.label} · {d.addresses.join(", ")}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          Evidence that these are one device
          <textarea name="reason" required minLength={10} maxLength={500} />
        </label>
        <p className="notice">
          Conflicting serial numbers block a merge. Connections return to
          review, and dedicated access must be verified for the resulting
          identity.
        </p>
        <div className="dialog-actions">
          <button className="primary" disabled={busy || !!selected.netbox}>
            Merge verified alias
          </button>
        </div>
      </form>
    );
  if (mode === "identity-split" && selected)
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          act(() =>
            api("/identity-splits", "POST", {
              device: { id: selected.id, revision: selected.revision },
              label: f.get("label"),
              addresses: f.getAll("address"),
              reason: f.get("reason"),
            }),
          );
        }}
      >
        <p>
          Move selected addresses from <strong>{selected.label}</strong> to a
          separate draft identity. Keep at least one address on the original.
        </p>
        <label className="field">
          New device name
          <input name="label" required maxLength={120} />
        </label>
        {selected.addresses.map((address: string) => (
          <label className="checkbox-field" key={address}>
            <input type="checkbox" name="address" value={address} />
            <span>{address}</span>
          </label>
        ))}
        <label className="field">
          Evidence for separating these addresses
          <textarea name="reason" required minLength={10} maxLength={500} />
        </label>
        <p className="notice">
          The new identity starts without an assumed model, role, serial number
          or verified access. Related connections need review.
        </p>
        <div className="dialog-actions">
          <button
            className="primary"
            disabled={
              busy || !!selected.netbox || selected.addresses.length < 2
            }
          >
            Split identity
          </button>
        </div>
      </form>
    );
  if ((mode === "identity-exclude" || mode === "identity-restore") && selected)
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          act(() =>
            api(
              `/devices/${selected.id}/disposition`,
              "POST",
              {
                status: mode === "identity-restore" ? "draft" : "excluded",
                reason: new FormData(e.currentTarget).get("reason"),
              },
              selected.revision,
            ),
          );
        }}
      >
        <p>
          {mode === "identity-restore" ? (
            <>
              Return <strong>{selected.label}</strong> to draft inventory for
              discovery and review. Measurements require accepted inventory and
              a fresh policy approval.
            </>
          ) : (
            <>
              Exclude <strong>{selected.label}</strong> from discovery and the
              current network view. Its evidence and history are retained;
              related measurement schedules are paused.
            </>
          )}
        </p>
        <label className="field">
          Reason
          <textarea name="reason" required minLength={3} maxLength={500} />
        </label>
        <p className="notice">
          This does not remove a device, account or configuration from the
          network, or delete its published inventory record.
        </p>
        <div className="dialog-actions">
          <button className="primary" disabled={busy}>
            {mode === "identity-restore"
              ? "Restore to draft inventory"
              : "Exclude identity"}
          </button>
        </div>
      </form>
    );
  if (mode === "connection" || mode === "add-connection")
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const body = Object.fromEntries(new FormData(e.currentTarget));
          act(() =>
            api(
              "/connections" + (selected ? "/" + selected.id : ""),
              selected ? "PATCH" : "POST",
              body,
              selected?.revision,
            ),
          );
        }}
      >
        <div className="form-row">
          {[
            ["source_id", "From device"],
            ["target_id", "To device"],
          ].map(([name, title]) => (
            <label className="field" key={name}>
              {title}
              <select
                name={name}
                required
                defaultValue={selected?.[name] ?? ""}
              >
                <option value="" disabled>
                  Choose a device
                </option>
                {devices
                  .filter((d) => d.status !== "merged")
                  .map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.label}
                    </option>
                  ))}
              </select>
            </label>
          ))}
        </div>
        <div className="form-row">
          <label className="field">
            Local port
            <input
              name="local_port"
              defaultValue={selected?.local_port ?? ""}
            />
          </label>
          <label className="field">
            Remote port
            <input
              name="remote_port"
              defaultValue={selected?.remote_port ?? ""}
            />
          </label>
        </div>
        <label className="field">
          Connection type
          <select name="layer" defaultValue={selected?.layer ?? "proposed"}>
            {[
              "advertised_neighbor",
              "logical_route",
              "service_dependency",
              "confirmed_physical",
              "proposed",
            ].map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Review decision
          <select
            name="decision"
            defaultValue={selected?.decision ?? "unresolved"}
          >
            <option value="unresolved">Keep unresolved</option>
            <option value="include">Include in next inventory review</option>
            <option value="rejected">Reject this connection</option>
          </select>
        </label>
        <label className="field">
          Evidence and reason
          <textarea name="reason" required minLength={3} maxLength={500} />
        </label>
        <p className="notice">
          Confirm a physical link only after checking both ports. Routing and
          neighbor advertisements alone do not prove a direct cable.
        </p>
        <div className="dialog-actions">
          <button className="primary" disabled={busy}>
            Save connection review
          </button>
        </div>
      </form>
    );
  return null;
}
