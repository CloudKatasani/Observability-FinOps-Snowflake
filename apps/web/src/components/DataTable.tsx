import type { ReactNode } from "react";

export interface Column {
  key: string;
  header: string;
  /** Numeric columns are right-aligned and set in tabular figures (§16.3). */
  numeric?: boolean;
  className?: string;
}

interface DataTableProps {
  caption: string;
  columns: readonly Column[];
  rows: readonly Record<string, ReactNode>[];
  /** Shown in place of the table body when there is nothing to show. */
  emptyReason?: string;
  dense?: boolean;
}

export default function DataTable({
  caption,
  columns,
  rows,
  emptyReason,
  dense = false,
}: DataTableProps) {
  if (rows.length === 0 && emptyReason) {
    return (
      <p className="rounded border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-700">
        {emptyReason}
      </p>
    );
  }

  const cell = dense ? "px-2 py-1" : "px-2 py-1.5";

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-slate-300">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`${cell} text-[11px] font-semibold tracking-wide text-slate-500 uppercase ${
                  column.numeric ? "text-right" : "text-left"
                } ${column.className ?? ""}`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-slate-100 last:border-0">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`${cell} align-top ${
                    column.numeric ? "text-right tabular-nums" : "text-left"
                  } ${column.className ?? ""}`}
                >
                  {row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
