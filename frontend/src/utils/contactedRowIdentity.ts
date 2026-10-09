/** A contact need is identified by exactly which manufacturer, which source
 *  attachment (InpharmD's permanent attachment id — not our S3 mirror url,
 *  which is regenerated per extraction), and which row of that attachment it
 *  came from — never by manufacturer or drug text alone (two rows can share
 *  both and still be different needs). */
export function rowContactKey(
  manufacturerId: number,
  attachmentId: number,
  sourceExcelRow: number,
): string {
  return `${manufacturerId}|${attachmentId}|${sourceExcelRow}`;
}

/** Manual-origin Inquiries never carry source_excel_attachment_id/source_excel_row
 *  — they are skipped entirely rather than given a synthetic key, so they can
 *  never match (and therefore never suppress) an attachment row. */
export function buildContactedRowKeys(
  inquiries: {
    manufacturer_id: number | null;
    source_excel_attachment_id?: number | null;
    source_excel_row?: number | null;
  }[],
): Set<string> {
  const keys = new Set<string>();
  for (const inq of inquiries) {
    if (inq.manufacturer_id == null || inq.source_excel_attachment_id == null || inq.source_excel_row == null) {
      continue;
    }
    keys.add(rowContactKey(inq.manufacturer_id, inq.source_excel_attachment_id, inq.source_excel_row));
  }
  return keys;
}

/** null when the row has no matched manufacturer — never "contacted" by definition. */
export function attachmentRowContactKey(
  manufacturerId: number | null,
  attachmentId: number,
  rowIndex: number,
): string | null {
  if (manufacturerId == null) return null;
  return rowContactKey(manufacturerId, attachmentId, rowIndex);
}

export function isRowContacted(
  contactedRowKeys: Set<string>,
  manufacturerId: number | null,
  attachmentId: number,
  rowIndex: number,
): boolean {
  const key = attachmentRowContactKey(manufacturerId, attachmentId, rowIndex);
  return key != null && contactedRowKeys.has(key);
}
