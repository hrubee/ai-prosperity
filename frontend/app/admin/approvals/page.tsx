"use client";

import { useEffect, useState } from "react";
import { AdminNav } from "@/components/AdminNav";
import { api, isAuthed, clearToken } from "@/lib/api";

interface ApprovalItem {
  user_id: string;
  name: string | null;
  email: string;
  phone: string | null;
  client_id?: string | null;
  payment_status: string;
}

export default function AdminApprovalsPage() {
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionStates, setActionStates] = useState<Record<string, "loading" | null>>({});
  const [searchQuery, setSearchQuery] = useState("");

  async function load() {
    try {
      const data = await api.adminApprovals();
      setApprovals(data);
    } catch (e: any) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = "/login";
      return;
    }
    load();
  }, []);

  async function handleApprove(userId: string, approve: boolean) {
    let note: string | undefined = undefined;
    if (!approve) {
      const input = window.prompt("Reason for rejection (optional):");
      if (input === null) return; // cancelled
      note = input;
    }
    
    setActionStates(prev => ({ ...prev, [userId]: "loading" }));
    try {
      await api.adminApprovePayment(userId, approve, note);
      await load();
    } catch (e: any) {
      alert(e.message || "Action failed");
    } finally {
      setActionStates(prev => ({ ...prev, [userId]: null }));
    }
  }

  function formatPhone(phone: string | null) {
    if (!phone) return "—";
    if (phone.length === 10) return `+91 ${phone.slice(0, 5)} ${phone.slice(5)}`;
    return phone;
  }

  const [tab, setTab] = useState<"pending" | "approved" | "revoked" | "all">("pending");

  const pendingCount = approvals.filter(a => a.payment_status === "pending" || a.payment_status === "pending_verification").length;
  const approvedCount = approvals.filter(a => a.payment_status === "approved").length;
  const revokedCount = approvals.filter(a => a.payment_status === "rejected" || a.payment_status === "revoked").length;

  const filteredApprovals = approvals.filter((a) => {
    const q = searchQuery.toLowerCase();
    const nameMatch = a.name?.toLowerCase().includes(q);
    const emailMatch = a.email?.toLowerCase().includes(q);
    const clientMatch = a.client_id?.toLowerCase().includes(q);
    const searchMatches = nameMatch || emailMatch || clientMatch;
    if (!searchMatches) return false;

    if (tab === "pending") return a.payment_status === "pending" || a.payment_status === "pending_verification";
    if (tab === "approved") return a.payment_status === "approved";
    if (tab === "revoked") return a.payment_status === "rejected" || a.payment_status === "revoked";
    return true; // "all"
  });

  return (
    <main className="min-h-screen">
      <AdminNav />

      <div className="container-x py-8">
        <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white">Client Access Approvals &amp; Management</h1>
            <p className="mt-1 text-sm text-muted">
              Review, approve, revoke, and manage customer registrations, Tradejini Client IDs, and access grants.
            </p>
          </div>
          <div className="flex flex-col sm:flex-row sm:items-center gap-4 text-sm">
            <input
              type="text"
              placeholder="Search name, email, client ID..."
              className="input text-sm w-full sm:w-64"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <button
              className="text-muted hover:text-white shrink-0"
              onClick={() => { clearToken(); window.location.href = "/"; }}
            >
              Log out
            </button>
          </div>
        </div>

        {/* Filter Tabs */}
        <div className="mb-6 flex flex-wrap items-center gap-2 border-b border-ink-800 pb-3">
          <button
            className={`btn-ghost !py-1.5 !px-3.5 text-xs font-semibold rounded-lg transition-all ${
              tab === "pending"
                ? "bg-warning/20 text-warning border-warning/40"
                : "text-muted hover:text-white"
            }`}
            onClick={() => setTab("pending")}
          >
            ⏳ Pending Approval ({pendingCount})
          </button>
          <button
            className={`btn-ghost !py-1.5 !px-3.5 text-xs font-semibold rounded-lg transition-all ${
              tab === "approved"
                ? "bg-gain/20 text-gain border-gain/40"
                : "text-muted hover:text-white"
            }`}
            onClick={() => setTab("approved")}
          >
            🟢 Approved Clients ({approvedCount})
          </button>
          <button
            className={`btn-ghost !py-1.5 !px-3.5 text-xs font-semibold rounded-lg transition-all ${
              tab === "revoked"
                ? "bg-loss/20 text-loss border-loss/40"
                : "text-muted hover:text-white"
            }`}
            onClick={() => setTab("revoked")}
          >
            🚫 Revoked / Rejected ({revokedCount})
          </button>
          <button
            className={`btn-ghost !py-1.5 !px-3.5 text-xs font-semibold rounded-lg transition-all ${
              tab === "all"
                ? "bg-gold-500/20 text-gold-400 border-gold-500/40"
                : "text-muted hover:text-white"
            }`}
            onClick={() => setTab("all")}
          >
            All Registrations ({approvals.length})
          </button>
        </div>

        {loading ? (
          <div className="card p-10 text-center text-muted">Loading pending approvals…</div>
        ) : filteredApprovals.length === 0 ? (
          <div className="card p-10 text-center text-muted">No client registrations found.</div>
        ) : (
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
            {filteredApprovals.map((item) => (
              <div key={item.user_id} className="card p-6 flex flex-col gap-4 relative hover:border-gold-500/40 transition-colors">
                <div className="flex justify-between items-start gap-4">
                  <div className="min-w-0">
                    <h3 className="font-semibold text-white text-lg truncate" title={item.name || "Unknown Name"}>{item.name || "Unknown Name"}</h3>
                    <p className="text-sm text-muted break-all">{item.email}</p>
                    <p className="text-sm font-mono mt-1 text-slate-300">{formatPhone(item.phone)}</p>
                  </div>
                  <span
                    className={`pill shrink-0 ${
                      item.payment_status === "approved"
                        ? "bg-gain/20 text-gain font-semibold"
                        : item.payment_status === "rejected"
                        ? "bg-loss/20 text-loss font-semibold"
                        : "bg-warning/20 text-warning font-semibold"
                    }`}
                  >
                    {item.payment_status === "approved" ? "Approved" : item.payment_status === "rejected" ? "Rejected" : "Pending Approval"}
                  </span>
                </div>
                
                <div className="rounded-xl bg-ink-900/60 p-4 border border-ink-800 space-y-2">
                  <div className="flex justify-between items-center text-xs text-muted">
                    <span>Tradejini Client ID</span>
                    <span className="font-mono text-sm font-bold text-gold-400">{item.client_id || "Not specified"}</span>
                  </div>
                </div>

                <div className="mt-auto pt-4 border-t border-ink-800">
                  {item.payment_status === "pending" || item.payment_status === "pending_verification" ? (
                    <div className="flex flex-wrap gap-3">
                      <button
                        className="btn-gold flex-1 text-sm py-2.5 font-semibold"
                        onClick={() => handleApprove(item.user_id, true)}
                        disabled={actionStates[item.user_id] === "loading"}
                      >
                        {actionStates[item.user_id] === "loading" ? "Approving…" : "Approve Access"}
                      </button>
                      <button
                        className="btn-ghost flex-1 text-sm py-2.5 text-loss border-loss/40 hover:bg-loss/10"
                        onClick={() => handleApprove(item.user_id, false)}
                        disabled={actionStates[item.user_id] === "loading"}
                      >
                        {actionStates[item.user_id] === "loading" ? "Rejecting…" : "Reject"}
                      </button>
                    </div>
                  ) : item.payment_status === "approved" ? (
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-gain flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full bg-gain" /> Access Approved
                      </span>
                      <button
                        className="btn-ghost text-xs text-loss border-loss/30 hover:bg-loss/10 py-1 px-3"
                        onClick={() => handleApprove(item.user_id, false)}
                      >
                        Revoke
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-loss flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full bg-loss" /> Access Rejected
                      </span>
                      <button
                        className="btn-gold text-xs py-1 px-3"
                        onClick={() => handleApprove(item.user_id, true)}
                      >
                        Approve Now
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}