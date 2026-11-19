import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Button, Card, ErrorNote, Field, Input } from "../components/ui";

const MAX_PHOTOS = 6;
const today = new Date().toISOString().slice(0, 10);

export default function SubmitClaim() {
  const { user } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [form, setForm] = useState({
    incident_date: "",
    incident_location: "",
    description: "",
    claimed_amount: "",
  });
  const [photos, setPhotos] = useState<File[]>([]);
  const previews = useMemo(() => photos.map((p) => URL.createObjectURL(p)), [photos]);
  useEffect(() => () => previews.forEach((u) => URL.revokeObjectURL(u)), [previews]);

  const submit = useMutation({
    mutationFn: () =>
      api.submit(
        {
          policy_number: user!.policy_number ?? "",
          incident_date: form.incident_date,
          incident_location: form.incident_location,
          description: form.description,
          claimed_amount: form.claimed_amount ? Number(form.claimed_amount) : null,
        },
        photos,
      ),
    onSuccess: ({ claim_id }) => {
      qc.invalidateQueries({ queryKey: ["claims"] });
      nav(`/claims/${claim_id}`);
    },
  });

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit.mutate();
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold">File a claim</h1>
      <Card>
        <form onSubmit={onSubmit} className="space-y-4">
          <Field label="Policy">
            <Input value={user?.policy_number ?? ""} disabled />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Date of incident">
              <Input type="date" max={today} value={form.incident_date} onChange={set("incident_date")} required />
            </Field>
            <Field label="Claimed amount (optional)" hint="Your own estimate, if you have one">
              <Input type="number" min={0} step="0.01" value={form.claimed_amount} onChange={set("claimed_amount")} />
            </Field>
          </div>
          <Field label="Where did it happen?">
            <Input
              value={form.incident_location}
              onChange={set("incident_location")}
              placeholder="e.g. Oak St and 5th Ave, Austin, TX"
              minLength={2}
              required
            />
          </Field>
          <Field label="What happened?" hint="At least 10 characters. Say what hit what and which parts are damaged.">
            <textarea
              value={form.description}
              onChange={set("description")}
              rows={4}
              minLength={10}
              required
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </Field>
          <Field label={`Photos of the damage (1–${MAX_PHOTOS}, JPEG or PNG)`}>
            <input
              type="file"
              accept="image/jpeg,image/png"
              multiple
              onChange={(e) => setPhotos(Array.from(e.target.files ?? []).slice(0, MAX_PHOTOS))}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium"
            />
          </Field>
          {previews.length > 0 && (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
              {previews.map((src, i) => (
                <img key={src} src={src} alt={photos[i].name} className="aspect-square rounded-md object-cover" />
              ))}
            </div>
          )}
          <ErrorNote error={submit.error} />
          <Button type="submit" disabled={submit.isPending || photos.length === 0}>
            {submit.isPending ? "Submitting…" : "Submit claim"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
