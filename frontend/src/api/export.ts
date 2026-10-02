/**
 * The one request that answers a file instead of JSON.
 *
 * It needs the session's token, which a plain `<a href>` cannot carry, so
 * the file comes back as a blob and is handed to the browser from here.
 */

import { api, unwrap } from "@/api/client";
import type { TransactionFilters } from "@/api/queries";
import { DISPLAY_TIMEZONE } from "@/lib/dates";
import { type ExportFormat, exportFileName, exportQuery } from "@/lib/exporting";

export async function downloadExport(
  filters: TransactionFilters,
  format: ExportFormat,
): Promise<void> {
  const blob = await unwrap(
    api.GET("/financial/export", {
      params: {
        query: { ...exportQuery(filters), format, timezone: DISPLAY_TIMEZONE },
      },
      parseAs: "blob",
    }),
  );

  const url = URL.createObjectURL(blob as Blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = exportFileName(format);
  document.body.append(link);
  link.click();
  link.remove();
  // Revoked later rather than at once: Safari and Firefox start the download
  // asynchronously and would find the URL already gone.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
