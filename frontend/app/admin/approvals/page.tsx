"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Logo } from "@/components/nav";
import { AdminNav } from "@/components/AdminNav";
import { api, isAuthed, clearToken } from "@/lib/api";

interface ApprovalItem {
  user_id: string;
  name: string | null;
  email: string;
  phone: string | null;
  payment_status: string;
  screenshot?: {
    status: string;
    mime_type: string;
    image_b64: string;
    created_at: string;
    reviewed_at: string | null;
    review_note: string | null;
  };
}

export default function AdminApprovalsPage() {
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionStates, setActionStates] = useState<Record<string, "loading" | null>>({});
  const [viewingImage, setViewingImage] = useState<string | null>(null);
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

  function formatDate(dateStr: string | null) {
    if (!dateStr) return "—";
    return new Date(dateStr).toLocaleString(undefined, {
      dateStyle: "short",
      timeStyle: "short",
    });
  }

  const pendingCount = approvals.filter(a => a.payment_status === "pending" || a.payment_status === "pending_verification").length;

  const filteredApprovals = approvals.filter((a) => {
    const q = searchQuery.toLowerCase();
    const nameMatch = a.name?.toLowerCase().includes(q);
    const emailMatch = a.email?.toLowerCase().includes(q);
    return nameMatch || emailMatch;
  });

  return (
    <main className="min-h-screen">
      <AdminNav />

      <div className="container-x py-8">
        <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white">Payment Approvals</h1>
            <p className="mt-1 text-sm text-muted">
              Review and approve/reject user payment screenshots for offline/UPI payments.
            </p>
          </div>
          <div className="flex flex-col sm:flex-row sm:items-center gap-4 text-sm">
            <input
              type="text"
              placeholder="Search by name or email..."
              className="input text-sm w-full sm:w-64"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <div className="flex items-center justify-between gap-3">
              <span className="pill bg-warning/20 text-warning shrink-0">
                {pendingCount} pending
              </span>
              <button
                className="text-muted hover:text-white shrink-0"
                onClick={() => { clearToken(); window.location.href = "/"; }}
              >
                Log out
              </button>
            </div>
          </div>
        </div>

        {loading ? (
          <div className="card p-10 text-center text-muted">Loading approvals…</div>
        ) : filteredApprovals.length === 0 ? (
          <div className="card p-10 text-center text-muted">No users found.</div>
        ) : (
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
            {filteredApprovals.map((item) => (
              <div key={item.user_id} className="card p-5 flex flex-col gap-4 relative">
                <div className="flex justify-between items-start gap-4">
                  <div className="min-w-0">
                    <h3 className="font-semibold text-white text-lg truncate" title={item.name || "Unknown Name"}>{item.name || "Unknown Name"}</h3>
                    <p className="text-sm text-muted break-all">{item.email}</p>
                    <p className="text-sm font-mono mt-1 text-slate-300">{formatPhone(item.phone)}</p>
                  </div>
                  <span
                    className={`pill shrink-0 ${
                      item.payment_status === "approved"
                        ? "bg-gain/20 text-gain"
                        : item.payment_status === "rejected" || item.payment_status === "pending_manual_payment"
                        ? "bg-loss/20 text-loss"
                        : "bg-warning/20 text-warning"
                    }`}
                  >
                    {item.payment_status}
                  </span>
                </div>
                
                <div className="rounded-lg bg-ink-900/50 p-4 border border-ink-800">
                  <h4 className="text-xs uppercase tracking-wider text-muted mb-3">Screenshot Proof</h4>
                  {item.screenshot ? (
                    <div className="flex flex-col gap-3">
                      <div className="flex items-center gap-2">
                        <span
                          className={`pill text-xs ${
                            item.screenshot.status === "approved"
                              ? "bg-gain/20 text-gain"
                              : item.screenshot.status === "rejected"
                              ? "bg-loss/20 text-loss"
                              : "bg-warning/20 text-warning"
                          }`}
                        >
                          {item.screenshot.status}
                        </span>
                        <span className="text-xs text-muted">
                          {formatDate(item.screenshot.created_at)}
                        </span>
                      </div>
                      {item.screenshot.image_b64 && (
                        <button
                          className="btn-ghost text-xs w-fit py-1.5"
                          onClick={() => setViewingImage(`data:${item.screenshot!.mime_type};base64,${item.screenshot!.image_b64}`)}
                        >
                          View Screenshot
                        </button>
                      )}
                      {item.screenshot.review_note && (
                        <p className="text-xs text-muted mt-1 italic border-l-2 border-ink-700 pl-2">
                          Note: {item.screenshot.review_note}
                        </p>
                      )}
                    </div>
                  ) : (
                    <span className="text-sm text-muted">No screenshot uploaded yet.</span>
                  )}
                </div>

                <div className="mt-auto pt-4 border-t border-ink-800">
                  {(item.payment_status === "pending" || item.payment_status === "pending_verification") && item.screenshot ? (
                    <div className="flex flex-wrap gap-3">
                      <button
                        className="btn-gold flex-1 text-sm py-2"
                        onClick={() => handleApprove(item.user_id, true)}
                        disabled={actionStates[item.user_id] === "loading"}
                      >
                        {actionStates[item.user_id] === "loading" ? "Approving…" : "Approve"}
                      </button>
                      <button
                        className="btn-ghost flex-1 text-sm py-2 text-loss border-loss/40 hover:bg-loss/10"
                        onClick={() => handleApprove(item.user_id, false)}
                        disabled={actionStates[item.user_id] === "loading"}
                      >
                        {actionStates[item.user_id] === "loading" ? "Rejecting…" : "Reject"}
                      </button>
                    </div>
                  ) : item.payment_status === "approved" ? (
                    <span className="text-sm font-semibold text-gain block text-center">✓ Payment Approved</span>
                  ) : item.payment_status === "rejected" || item.payment_status === "pending_manual_payment" ? (
                    <span className="text-sm font-semibold text-loss block text-center">✗ Payment Rejected</span>
                  ) : (
                    <span className="text-sm text-muted block text-center">Awaiting user action</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {viewingImage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={() => setViewingImage(null)}>
          <img 
            src={viewingImage} 
            alt="Payment Screenshot" 
            className="max-h-[90vh] max-w-[90vw] rounded-xl object-contain border border-ink-700 bg-ink-900" 
            onClick={e => e.stopPropagation()} 
          />
          <button 
            className="absolute top-4 right-4 text-white hover:text-gray-300 rounded-full bg-black/50 p-2" 
            onClick={() => setViewingImage(null)}
          >
            ✕ Close
          </button>
        </div>
      )}
    </main>
  );
}