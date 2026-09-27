"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, type Repository } from "@/lib/api";

import { Button, Card, ErrorState, Field, inputClass } from "./ui";

type Source = "sample" | "github";

function repoLabel(repo: Repository) {
  return repo.source === "local" ? `${repo.name} (sample)` : `${repo.owner}/${repo.name}`;
}

export function NewRunForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const samples = useQuery({ queryKey: ["samples"], queryFn: api.samples });
  const [source, setSource] = useState<Source>("sample");
  const [sample, setSample] = useState("");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [number, setNumber] = useState("");
  const [model, setModel] = useState("");
  const [maxCost, setMaxCost] = useState("2.00");
  const [maxAttempts, setMaxAttempts] = useState("3");

  const create = useMutation({
    mutationFn: async () => {
      const repo = await api.addRepository(
        source === "sample" ? { sample: sample || samples.data?.[0] || "" } : { url: url.trim() },
      );
      return api.createRun({
        repository_id: repo.id,
        issue: { title: title.trim(), body, number: number ? Number(number) : null },
        model: model.trim() || null,
        budget: { max_cost_usd: maxCost, max_fix_attempts: Number(maxAttempts) },
      });
    },
    onSuccess: async (run) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/runs/${run.id}`);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };

  return (
    <Card title="Start a run">
      <form onSubmit={submit} className="flex flex-col gap-4 p-4">
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Repository</legend>
          <div className="flex gap-2 text-sm" role="radiogroup">
            {(["sample", "github"] as const).map((value) => (
              <label
                key={value}
                className="flex cursor-pointer items-center gap-2 rounded-md border border-zinc-300 px-3 py-1.5 has-checked:border-indigo-500 has-checked:bg-indigo-50 dark:border-zinc-700 dark:has-checked:bg-indigo-950/40"
              >
                <input
                  type="radio"
                  name="source"
                  value={value}
                  checked={source === value}
                  onChange={() => setSource(value)}
                  className="accent-indigo-600"
                />
                {value === "sample" ? "Bundled sample" : "Public GitHub repository"}
              </label>
            ))}
          </div>
          {source === "sample" ? (
            <select
              aria-label="Sample repository"
              className={inputClass}
              value={sample || samples.data?.[0] || ""}
              onChange={(e) => setSample(e.target.value)}
              disabled={!samples.data?.length}
            >
              {samples.isPending && <option>Loading samples…</option>}
              {samples.data?.length === 0 && <option value="">No samples bundled</option>}
              {samples.data?.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : (
            <input
              aria-label="Repository URL"
              className={inputClass}
              placeholder="https://github.com/owner/repo"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
            />
          )}
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
          <Field label="Issue title">
            <input
              className={inputClass}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="slugify crashes on titles without letters"
              required
              maxLength={500}
            />
          </Field>
          <Field label="Issue #" hint="Optional">
            <input
              className={inputClass}
              inputMode="numeric"
              pattern="[0-9]*"
              value={number}
              onChange={(e) => setNumber(e.target.value)}
            />
          </Field>
        </div>
        <Field label="Issue body">
          <textarea
            className={`${inputClass} min-h-32 font-mono text-xs`}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Steps to reproduce, expected and actual behavior, tracebacks…"
          />
        </Field>

        <details className="text-sm">
          <summary className="cursor-pointer text-zinc-500 select-none">Model and budget</summary>
          <div className="mt-3 grid gap-4 sm:grid-cols-3">
            <Field label="Model" hint="Empty uses the server default">
              <input
                className={inputClass}
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            </Field>
            <Field label="Max cost (USD)">
              <input
                className={inputClass}
                inputMode="decimal"
                value={maxCost}
                onChange={(e) => setMaxCost(e.target.value)}
              />
            </Field>
            <Field label="Max fix attempts">
              <input
                className={inputClass}
                type="number"
                min={0}
                max={20}
                value={maxAttempts}
                onChange={(e) => setMaxAttempts(e.target.value)}
              />
            </Field>
          </div>
        </details>

        {create.isError && <ErrorState title="Could not start the run" error={create.error} />}
        <div className="flex items-center justify-end gap-3">
          <p className="text-xs text-zinc-500">
            The agent works in a sandbox and stops for your approval before any pull request.
          </p>
          <Button type="submit" variant="primary" disabled={create.isPending || !title.trim()}>
            {create.isPending ? "Starting…" : "Start run"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

export { repoLabel };
