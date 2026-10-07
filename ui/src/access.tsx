import { ShieldCheck, Plus, KeyRound, Fingerprint } from "lucide-react";
import { api, label, time, type RecordData } from "./api";

type Action = (
  operation: () => Promise<unknown>,
  close?: boolean,
) => Promise<void>;
type Open = (modal: string, item?: RecordData) => void;

export function AccessPanel({
  profiles,
  hosts,
  holds,
  open,
}: {
  profiles: RecordData[];
  hosts: RecordData[];
  holds: RecordData[];
  open: Open;
}) {
  return (
    <div className="access-grid">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Automation accounts</h2>
            <p>One account and key policy for each infrastructure range.</p>
          </div>
          <button onClick={() => open("access-profile")}>
            <Plus size={16} /> Add profile
          </button>
        </div>
        <div className="panel-foot">
          A profile prepares a key. Creating its account on a device is a
          separate, approved enrollment.
        </div>
        {profiles.length ? (
          <div className="activity-list">
            {profiles.map((p) => (
              <button
                key={p.id}
                onClick={() => open("access-profile-detail", p)}
              >
                <span className="device-icon">
                  <KeyRound size={18} />
                </span>
                <div>
                  <strong>{p.name}</strong>
                  <small>
                    {p.username} · {p.prefixes.join(", ")}
                  </small>
                </div>
                <span className="badge">
                  {label(p.key_status ?? "not_prepared")}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty">
            <ShieldCheck size={26} />
            <h3>Set up key-based access</h3>
            <p>
              Create an automation profile, review host fingerprints, then
              discover with that profile. Device login passwords are never
              stored here.
            </p>
          </div>
        )}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Device host identities</h2>
            <p>Check who you are connecting to before authentication.</p>
          </div>
          <button
            disabled={!profiles.some((p) => p.key_status === "ready")}
            onClick={() => open("host-scan")}
          >
            <Fingerprint size={16} /> Collect fingerprint
          </button>
        </div>
        {hosts.length ? (
          <div className="activity-list">
            {hosts.map((h) => (
              <button key={h.id} onClick={() => open("host-review", h)}>
                <span className="device-icon">
                  <Fingerprint size={18} />
                </span>
                <div>
                  <strong>{h.address}</strong>
                  <small>
                    {profiles.find((p) => p.id === h.profile_id)?.name} ·{" "}
                    {time(h.observed_at)}
                  </small>
                </div>
                <span className="badge">{label(h.status)}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty">
            <h3>No host identities reviewed</h3>
            <p>
              Collection reads public SSH host keys only. Compare fingerprints
              with a trusted source, or explicitly record acceptance on first
              use.
            </p>
          </div>
        )}
      </section>
      {holds.some((h) => !h.released_at) && (
        <section className="panel">
          <div className="panel-heading">
            <h2>Access needs attention</h2>
          </div>
          <div className="activity-list">
            {holds
              .filter((h) => !h.released_at)
              .map((h) => (
                <button key={h.id} onClick={() => open("access-hold", h)}>
                  <div>
                    <strong>{h.address}</strong>
                    <small>{h.reason}</small>
                  </div>
                  <span className="badge">held</span>
                </button>
              ))}
          </div>
        </section>
      )}
    </div>
  );
}

export function AccessForm({
  mode,
  profiles,
  selected,
  busy,
  act,
}: {
  mode: string;
  profiles: RecordData[];
  selected: RecordData | null;
  busy: boolean;
  act: Action;
}) {
  if (mode === "access-profile")
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          act(() =>
            api("/access-profiles", "POST", {
              name: f.get("name"),
              username: f.get("username"),
              prefixes: String(f.get("prefixes"))
                .split(",")
                .map((v) => v.trim()),
              platform: f.get("platform"),
              port: Number(f.get("port")),
              key_algorithm: f.get("key_algorithm"),
            }),
          );
        }}
      >
        <label className="field">
          Profile name
          <input
            name="name"
            required
            maxLength={80}
            placeholder="Building switches"
          />
        </label>
        <label className="field">
          Dedicated account name
          <input
            name="username"
            required
            pattern="[A-Za-z_][A-Za-z0-9_.-]{0,63}"
            placeholder="network_ops"
          />
          <small>
            Use the account intended for this application. Existing
            administrator access remains available.
          </small>
        </label>
        <label className="field">
          Management ranges
          <input name="prefixes" required placeholder="192.0.2.0/24" />
        </label>
        <div className="form-row">
          <label className="field">
            Platform
            <select name="platform">
              <option value="auto">Identify from response</option>
              <option value="cisco_iosxe">Cisco IOS / IOS XE</option>
              <option value="dlink">D-Link</option>
              <option value="linux">Linux</option>
              <option value="freebsd">FreeBSD</option>
            </select>
          </label>
          <label className="field">
            SSH port
            <input
              name="port"
              type="number"
              defaultValue={22}
              min={1}
              max={65535}
              required
            />
          </label>
        </div>
        <label className="field">
          New key type
          <select name="key_algorithm">
            <option value="ed25519">Ed25519</option>
            <option value="rsa3072">
              RSA 3072 — older device compatibility
            </option>
          </select>
          <small>
            The worker generates a new private key. Only its public key and
            fingerprint appear in the application.
          </small>
        </label>
        <div className="dialog-actions">
          <button className="primary" disabled={busy}>
            Create profile and key
          </button>
        </div>
      </form>
    );
  if (mode === "access-profile-detail" && selected)
    return (
      <>
        <dl className="definition-list">
          <dt>Account</dt>
          <dd>{selected.username}</dd>
          <dt>Management ranges</dt>
          <dd>{selected.prefixes.join(", ")}</dd>
          <dt>Key preparation</dt>
          <dd>{label(selected.key_status)}</dd>
          <dt>Fingerprint</dt>
          <dd className="fingerprint">
            {selected.fingerprint ?? "Not prepared"}
          </dd>
        </dl>
        {selected.public_key && (
          <>
            <h3>Public key</h3>
            <pre className="public-key">{selected.public_key}</pre>
          </>
        )}
        <p className="notice">
          This does not prove the account exists on any device. Discovery
          verifies login only after a host identity is reviewed. Device account
          enrollment requires its own backed-up change plan.
        </p>
      </>
    );
  if (mode === "host-scan")
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          act(() =>
            api(
              "/host-identities",
              "POST",
              Object.fromEntries(new FormData(e.currentTarget)),
            ),
          );
        }}
      >
        <label className="field">
          Access profile
          <select name="profile_id" required>
            {profiles
              .filter((p) => p.key_status === "ready")
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          Infrastructure IP address
          <input name="address" required placeholder="192.0.2.1" />
        </label>
        <p className="notice">
          A bounded SSH host-key scan runs against this one address. It sends no
          username, password or private key.
        </p>
        <div className="dialog-actions">
          <button className="primary" disabled={busy}>
            Collect fingerprint
          </button>
        </div>
      </form>
    );
  if (mode === "host-review" && selected)
    return (
      <>
        <p>
          <strong>{selected.address}</strong> · {label(selected.status)} ·{" "}
          {time(selected.observed_at)}
        </p>
        <div className="fingerprint-list">
          {selected.fingerprints?.map(
            (f: { algorithm: string; fingerprint: string }) => (
              <div key={f.fingerprint}>
                <strong>{f.algorithm}</strong>
                <code>{f.fingerprint}</code>
              </div>
            ),
          )}
        </div>
        {selected.status === "awaiting_review" ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(() =>
                api(
                  `/host-identities/${selected.id}/approve`,
                  "POST",
                  {
                    fingerprint_digest: selected.fingerprint_digest,
                    basis: f.get("basis"),
                    note: f.get("note"),
                  },
                  selected.revision,
                ),
              );
            }}
          >
            <label className="field">
              How was this identity checked?
              <select name="basis" required defaultValue="">
                <option value="" disabled>
                  Select a verification basis
                </option>
                <option value="independently_verified">
                  Compared with a trusted source
                </option>
                <option value="accepted_first_use">
                  Accept on first use — not independently verified
                </option>
              </select>
            </label>
            <label className="field">
              Verification note
              <textarea
                name="note"
                required
                minLength={3}
                maxLength={300}
                placeholder="Record how you checked the fingerprint, or why you accept first use."
              />
            </label>
            <p className="notice">
              Approval applies to these exact keys and this access profile. A
              different host key will stop authentication.
            </p>
            <div className="dialog-actions">
              <button className="primary" disabled={busy}>
                Approve this host identity
              </button>
            </div>
          </form>
        ) : (
          <p>
            {selected.note ?? "Refresh this list when collection finishes."}
          </p>
        )}
      </>
    );
  if (mode === "access-hold" && selected)
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          act(() =>
            api(
              `/access-holds/${selected.id}/release`,
              "POST",
              Object.fromEntries(new FormData(e.currentTarget)),
              selected.revision,
            ),
          );
        }}
      >
        <p>
          {selected.address} · {selected.reason}
        </p>
        <label className="field">
          What has been corrected?
          <textarea name="reason" required minLength={3} maxLength={300} />
        </label>
        <p className="notice">
          Releasing the hold permits the next requested discovery to try
          key-based access again. It does not start a login attempt now.
        </p>
        <div className="dialog-actions">
          <button className="primary" disabled={busy}>
            Release access hold
          </button>
        </div>
      </form>
    );
  return null;
}
