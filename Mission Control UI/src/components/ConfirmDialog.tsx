export default function ConfirmDialog({
  title, body, confirmLabel, onConfirm, onCancel, busy,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onClick={onCancel}>
      <div className="w-full max-w-sm bg-neutral-900 border border-white/15 rounded-md p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <p className="mt-2 text-sm text-neutral-400">{body}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onCancel} disabled={busy} className="text-xs font-medium px-3 py-1.5 rounded border border-white/15 text-neutral-300 hover:bg-white/10 disabled:opacity-50">
            Cancel
          </button>
          <button onClick={onConfirm} disabled={busy} className="text-xs font-medium px-3 py-1.5 rounded bg-accent text-white hover:bg-accent/90 disabled:opacity-50">
            {busy ? "Sending…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
