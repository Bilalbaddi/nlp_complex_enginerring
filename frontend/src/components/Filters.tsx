"use client";

import { useApi } from "@/lib/hooks";
import { CATEGORIES, label } from "@/lib/format";
import type { Department, Ward } from "@/lib/types";
import { Field, Select } from "./ui";

export function useWards(): Ward[] {
  return useApi<Ward[]>("/wards").data ?? [];
}

export function useDepartments(): Department[] {
  return useApi<Department[]>("/departments").data ?? [];
}

export function WardSelect({
  value,
  onChange,
  wards,
  all = "All wards",
  fieldLabel = "Ward",
}: {
  value: string;
  onChange: (v: string) => void;
  wards: Ward[];
  all?: string;
  fieldLabel?: string;
}): JSX.Element {
  return (
    <Field label={fieldLabel}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{all}</option>
        {wards.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name} ({w.code})
          </option>
        ))}
      </Select>
    </Field>
  );
}

export function CategorySelect({
  value,
  onChange,
  all = "All categories",
  fieldLabel = "Category",
  required = false,
}: {
  value: string;
  onChange: (v: string) => void;
  all?: string;
  fieldLabel?: string;
  required?: boolean; // no "all" option: a specific category must be chosen
}): JSX.Element {
  return (
    <Field label={fieldLabel}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {!required && <option value="">{all}</option>}
        {CATEGORIES.map((c) => (
          <option key={c} value={c}>
            {label(c)}
          </option>
        ))}
      </Select>
    </Field>
  );
}
