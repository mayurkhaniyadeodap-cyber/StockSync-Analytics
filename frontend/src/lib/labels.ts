/**
 * Names for values the API returns as identifiers.
 *
 * Shared so a method reads the same on every screen that shows it: Import
 * History and the Settings preview of it are the same list, and two copies of
 * this map would eventually disagree about what `csv_url` is called.
 */

/** Mirrors IMPORT_METHODS in backend/app/models/inventory.py. */
const METHOD_LABELS: Record<string, string> = {
  csv_upload: 'CSV upload',
  excel_upload: 'Excel upload',
  google_sheet: 'Google Sheet',
};

export function methodLabel(method: string): string {
  return METHOD_LABELS[method] ?? method;
}

/**
 * What an import warning means, in the user's terms.
 *
 * The server sends a stable code and the sentence lives here, next to the screen
 * that renders it. A warning is not a failure: the rows landed. It says the file
 * was not read the way the user probably meant it to be, which is the case that
 * otherwise goes unnoticed until a figure looks wrong weeks later.
 */
export const IMPORT_WARNINGS: Record<string, { title: string; body: string }> = {
  date_column_ignored: {
    title: 'The Date column was not used.',
    body:
      'This sheet has no Reason column, so it was read as one row per SKU with running ' +
      'totals — a format that has no dates in it. To filter complaints by date, import ' +
      'the complaint export instead: one row per complaint, with a Reason column.',
  },
  date_column_ignored_with_duplicates: {
    title: 'The Date column was not used, and rows for the same SKU were combined.',
    body:
      'This sheet has no Reason column, so it was read as one row per SKU. Rows sharing a ' +
      'SKU were merged and their quantities added together — if this file has one row per ' +
      'SKU per day, those quantities are now summed across days. Check the merged rows ' +
      'below, and import the complaint export if you want complaints filtered by date.',
  },
};
