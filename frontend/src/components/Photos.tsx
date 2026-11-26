import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, CardTitle, Spinner } from "./ui";

// Images need the Authorization header, so they are fetched as blobs, not <img src=url>.
function useBlobUrl(claimId: string, name: string | null, size: "thumb" | "full") {
  const q = useQuery({
    queryKey: ["photo", claimId, name, size],
    queryFn: () => api.photoBlob(claimId, name!, size),
    enabled: Boolean(name),
    staleTime: Infinity,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!q.data) return;
    const u = URL.createObjectURL(q.data);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [q.data]);
  return { url, loading: q.isLoading };
}

function Thumb({ claimId, name, onOpen }: { claimId: string; name: string; onOpen: () => void }) {
  const { url } = useBlobUrl(claimId, name, "thumb");
  return (
    <button
      onClick={onOpen}
      className="aspect-square overflow-hidden rounded-md bg-slate-100 ring-blue-500 hover:ring-2 focus:outline-none focus-visible:ring-2"
      aria-label="Open photo"
    >
      {url ? <img src={url} alt="Damage photo" className="h-full w-full object-cover" /> : <Spinner />}
    </button>
  );
}

function Viewer({ claimId, name, onClose }: { claimId: string; name: string; onClose: () => void }) {
  const { url } = useBlobUrl(claimId, name, "full");
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      className="fixed inset-0 z-50 grid place-items-center bg-slate-900/80 p-4"
    >
      {url ? (
        <img src={url} alt="Damage photo, full size" className="max-h-full max-w-full rounded-md shadow-xl" />
      ) : (
        <span className="text-white">
          <Spinner />
        </span>
      )}
    </div>
  );
}

export function Photos({ claimId }: { claimId: string }) {
  const q = useQuery({ queryKey: ["photos", claimId], queryFn: () => api.photos(claimId) });
  const [open, setOpen] = useState<string | null>(null);
  if (!q.data?.length) return null;
  return (
    <Card>
      <CardTitle>Photos</CardTitle>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {q.data.map((n) => (
          <Thumb key={n} claimId={claimId} name={n} onOpen={() => setOpen(n)} />
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-500">Shown without location metadata.</p>
      {open && <Viewer claimId={claimId} name={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}
