// Built-in SQL function catalog + parameter-hint engine (pure JS, no Monaco — unit tested).
//
// Every built-in function is described per dialect with a compact signature spec:
//
//     "DATEADD(datepart:@mssql_datepart, number, date) -> same type as date | Adds …"
//
//   * `[ , param ]`  optional parameter        * `...name`  repeatable (variadic) parameter
//   * `name:@enum`   the parameter takes one of a fixed set of values (listed while typing)
//   * ` AS `, ` FROM `, ` FOR `, ` USING `, ` IN `, ` PLACING ` separate parameters the way the
//     SQL syntax does (CAST(x AS type), EXTRACT(field FROM source), …)
//   * `NAME() OVER`  window function — inserted with an OVER (…) clause
//   * the same name listed twice = overloads (e.g. TRIM(string) / TRIM(characters FROM string))
//
// The engine finds the function call around the cursor (skipping strings, comments and nested
// calls), picks the matching overload and the active parameter — the data behind Monaco's
// signature help (parameter hints), parameter-value completion and function hover.

import { statementBounds, tokenize } from "./sql-intellisense.js";

// --- parameter value sets --------------------------------------------------------------------

const MSSQL_DATEPARTS = [
  ["year", "Year (abbreviations: yy, yyyy)"], ["quarter", "Quarter (qq, q)"], ["month", "Month (mm, m)"],
  ["dayofyear", "Day of the year (dy, y)"], ["day", "Day of the month (dd, d)"], ["week", "Week (wk, ww)"],
  ["weekday", "Day of the week (dw, w)"], ["hour", "Hour (hh)"], ["minute", "Minute (mi, n)"],
  ["second", "Second (ss, s)"], ["millisecond", "Millisecond (ms)"], ["microsecond", "Microsecond (mcs)"],
  ["nanosecond", "Nanosecond (ns)"],
  ["yy", "year"], ["yyyy", "year"], ["qq", "quarter"], ["mm", "month"], ["dy", "dayofyear"], ["dd", "day"],
  ["wk", "week"], ["dw", "weekday"], ["hh", "hour"], ["mi", "minute"], ["ss", "second"], ["ms", "millisecond"],
];

export const ENUMS = {
  // `choices`: the values offered in the inserted snippet's dropdown (full names, most common first).
  mssql_datepart: {
    values: MSSQL_DATEPARTS,
    choices: ["day", "month", "year", "week", "quarter", "dayofyear", "weekday", "hour", "minute", "second", "millisecond", "microsecond", "nanosecond"],
  },
  mssql_datepart_ext: {
    values: [...MSSQL_DATEPARTS, ["iso_week", "ISO 8601 week number (isowk, isoww)"], ["tzoffset", "Time-zone offset in minutes (tz)"]],
    choices: ["year", "quarter", "month", "day", "week", "weekday", "dayofyear", "hour", "minute", "second", "millisecond", "iso_week", "tzoffset"],
  },
  mssql_style: {
    values: [
      ["23", "yyyy-mm-dd (ISO date)"], ["101", "mm/dd/yyyy (US)"], ["103", "dd/mm/yyyy (British/French)"],
      ["104", "dd.mm.yyyy (German)"], ["105", "dd-mm-yyyy (Italian)"], ["106", "dd mon yyyy"],
      ["107", "Mon dd, yyyy"], ["108", "hh:mi:ss"], ["110", "mm-dd-yyyy (USA)"], ["111", "yyyy/mm/dd (Japan)"],
      ["112", "yyyymmdd (ISO basic)"], ["114", "hh:mi:ss:mmm (24h)"], ["120", "yyyy-mm-dd hh:mi:ss (ODBC canonical)"],
      ["121", "yyyy-mm-dd hh:mi:ss.mmm (ODBC with ms)"], ["126", "yyyy-mm-ddThh:mi:ss.mmm (ISO 8601)"],
      ["127", "ISO 8601 with time zone Z"], ["0", "mon dd yyyy hh:miAM (default)"], ["1", "mm/dd/yy"], ["3", "dd/mm/yy"],
    ],
  },
  mssql_type: {
    values: [
      ["int", "32-bit integer"], ["bigint", "64-bit integer"], ["smallint", "16-bit integer"], ["tinyint", "0–255"],
      ["bit", "0 / 1"], ["decimal(18, 2)", "Exact numeric (precision, scale)"], ["numeric(18, 2)", "Exact numeric"],
      ["money", "Currency"], ["float", "Approximate numeric"], ["real", "Approximate numeric (4 bytes)"],
      ["date", "Date"], ["time", "Time of day"], ["datetime", "Date and time (3.33 ms)"],
      ["datetime2", "Date and time (100 ns)"], ["datetimeoffset", "Date and time with time zone"],
      ["smalldatetime", "Date and time (1 minute)"], ["char(10)", "Fixed-length text"], ["varchar(50)", "Variable-length text"],
      ["varchar(max)", "Large text"], ["nchar(10)", "Fixed-length Unicode"], ["nvarchar(50)", "Variable-length Unicode"],
      ["nvarchar(max)", "Large Unicode text"], ["uniqueidentifier", "GUID"], ["varbinary(max)", "Binary data"],
      ["xml", "XML document"],
    ],
  },
  mssql_hash: {
    quote: true,
    values: [["SHA2_256", "SHA-256 (recommended)"], ["SHA2_512", "SHA-512"], ["SHA1", "SHA-1 (legacy)"], ["MD5", "MD5 (legacy)"]],
  },
  pg_field: {
    values: [
      ["year", "Year"], ["quarter", "Quarter (1–4)"], ["month", "Month (1–12)"], ["week", "ISO week number"],
      ["day", "Day of month"], ["dow", "Day of week (0 = Sunday)"], ["isodow", "ISO day of week (1 = Monday)"],
      ["doy", "Day of year"], ["hour", "Hour"], ["minute", "Minute"], ["second", "Seconds incl. fraction"],
      ["milliseconds", "Milliseconds"], ["microseconds", "Microseconds"], ["epoch", "Seconds since 1970-01-01"],
      ["decade", "Decade"], ["century", "Century"], ["millennium", "Millennium"], ["timezone", "UTC offset in seconds"],
      ["isoyear", "ISO 8601 week-numbering year"],
    ],
  },
  pg_field_quoted: { quote: true, ref: "pg_field" },
  pg_trunc: {
    quote: true,
    values: [
      ["year", "Start of the year"], ["quarter", "Start of the quarter"], ["month", "Start of the month"],
      ["week", "Monday of the week"], ["day", "Midnight"], ["hour", "Start of the hour"], ["minute", "Start of the minute"],
      ["second", "Whole second"], ["milliseconds", "Millisecond"], ["microseconds", "Microsecond"],
      ["decade", "Decade"], ["century", "Century"], ["millennium", "Millennium"],
    ],
  },
  pg_format: {
    quote: true,
    values: [
      ["YYYY-MM-DD", "2026-11-12"], ["DD/MM/YYYY", "12/11/2026"], ["YYYY-MM-DD HH24:MI:SS", "2026-11-12 18:05:09"],
      ["DD Mon YYYY", "12 Nov 2026"], ["Day, DD Month YYYY", "Thursday, 12 November 2026"], ["HH24:MI", "18:05"],
      ["FM999,999,990.00", "Number with thousands separator"], ["YYYY\"Q\"Q", "2026Q4"],
    ],
  },
  pg_type: {
    values: [
      ["integer", "32-bit integer"], ["bigint", "64-bit integer"], ["smallint", "16-bit integer"],
      ["numeric(12, 2)", "Exact numeric"], ["real", "4-byte float"], ["double precision", "8-byte float"],
      ["text", "Unlimited text"], ["varchar(50)", "Variable-length text"], ["char(10)", "Fixed-length text"],
      ["boolean", "true / false"], ["date", "Date"], ["time", "Time of day"], ["timestamp", "Date and time"],
      ["timestamptz", "Date and time with time zone"], ["interval", "Time span"], ["uuid", "UUID"],
      ["json", "JSON text"], ["jsonb", "Binary JSON"], ["bytea", "Binary data"],
    ],
  },
  my_unit: {
    values: [
      ["DAY", "Days"], ["MONTH", "Months"], ["YEAR", "Years"], ["HOUR", "Hours"], ["MINUTE", "Minutes"],
      ["SECOND", "Seconds"], ["WEEK", "Weeks"], ["QUARTER", "Quarters"], ["MICROSECOND", "Microseconds"],
      ["YEAR_MONTH", "'years-months'"], ["DAY_HOUR", "'days hours'"], ["HOUR_MINUTE", "'hours:minutes'"],
      ["MINUTE_SECOND", "'minutes:seconds'"], ["DAY_SECOND", "'days hours:minutes:seconds'"],
    ],
  },
  my_type: {
    values: [
      ["SIGNED", "Signed 64-bit integer"], ["UNSIGNED", "Unsigned 64-bit integer"], ["DECIMAL(12, 2)", "Exact numeric"],
      ["DOUBLE", "8-byte float"], ["FLOAT", "4-byte float"], ["CHAR(50)", "Text"], ["NCHAR(50)", "National text"],
      ["DATE", "Date"], ["DATETIME", "Date and time"], ["TIME", "Time"], ["YEAR", "Year"], ["JSON", "JSON value"],
      ["BINARY", "Binary string"],
    ],
  },
  my_format: {
    quote: true,
    values: [
      ["%Y-%m-%d", "2026-11-12"], ["%d/%m/%Y", "12/11/2026"], ["%Y-%m-%d %H:%i:%s", "2026-11-12 18:05:09"],
      ["%d %b %Y", "12 Nov 2026"], ["%W, %e %M %Y", "Thursday, 12 November 2026"], ["%H:%i", "18:05"],
      ["%Y%m%d", "20261112"],
    ],
  },
  my_charset: { values: [["utf8mb4", "Full Unicode"], ["latin1", "Western European"], ["ascii", "ASCII"], ["binary", "Binary"]] },
  sqlite_modifier: {
    quote: true,
    values: [
      ["now", "Current date/time (as the time value)"], ["start of month", "First day of the month"],
      ["start of year", "First day of the year"], ["start of day", "Midnight"], ["+1 day", "Add days (any number)"],
      ["-1 day", "Subtract days"], ["+1 month", "Add months"], ["+1 year", "Add years"], ["+1 hour", "Add hours"],
      ["weekday 1", "Next Monday (0 = Sunday)"], ["unixepoch", "Value is seconds since 1970"],
      ["localtime", "Convert UTC to local time"], ["utc", "Convert local time to UTC"],
    ],
  },
  sqlite_format: {
    quote: true,
    values: [
      ["%Y-%m-%d", "2026-11-12"], ["%Y-%m-%d %H:%M:%S", "2026-11-12 18:05:09"], ["%d/%m/%Y", "12/11/2026"],
      ["%H:%M", "18:05"], ["%Y", "Year"], ["%m", "Month 01–12"], ["%d", "Day 01–31"], ["%w", "Weekday 0–6 (Sunday = 0)"],
      ["%j", "Day of year"], ["%W", "Week of year"], ["%s", "Seconds since 1970"],
    ],
  },
  sqlite_type: {
    values: [["INTEGER", "Integer"], ["REAL", "Floating point"], ["TEXT", "Text"], ["NUMERIC", "Numeric affinity"], ["BLOB", "Binary"]],
  },
};

// Short descriptions for parameter names used across many functions (shown under the hint).
const PARAM_DOCS = {
  expression: "Any valid expression.", scalar_expression: "Value to return.", number: "How many units to add (negative to subtract).",
  date: "A date/time value, column or expression.", startdate: "Start date/time.", enddate: "End date/time.",
  datepart: "The part of the date to work with.", data_type: "The target data type.", style: "Optional conversion style code.",
  string: "A text value, column or expression.", string_expression: "A text value, column or expression.",
  character_expression: "A text value, column or expression.", separator: "Text placed between values.",
  delimiter: "Text placed between values.", length: "Number of characters.", start: "1-based start position.",
  format: "Format pattern.", culture: "Culture name, e.g. 'en-GB' (optional).", check_expression: "Value tested for NULL.",
  replacement_value: "Returned when the first argument is NULL.", offset: "Rows back/forward (default 1).",
  default: "Returned when the offset row does not exist.", partition: "Rows to divide into groups.",
  boolean_expression: "Condition to test.", true_value: "Returned when the condition is true.",
  false_value: "Returned when the condition is false.", field: "Which part of the date to extract.",
  source: "A date, timestamp or interval.", unit: "Unit of time.", path: "JSON path, e.g. '$.name'.",
  json_doc: "A JSON document.", time_value: "A date/time string, 'now', or a number.", modifier: "Shifts or adjusts the time value.",
  precision: "Digits of fractional precision.", seed: "Optional seed for repeatable results.",
};

// --- per-dialect function lists ----------------------------------------------------------------

const WINDOW = [
  "ROW_NUMBER() OVER -> bigint | Sequential number of the row within its partition, starting at 1.",
  "RANK() OVER -> bigint | Rank of the row, with gaps after ties.",
  "DENSE_RANK() OVER -> bigint | Rank of the row, without gaps after ties.",
  "NTILE(groups) OVER -> bigint | Distributes rows into the given number of ranked groups.",
  "LAG(scalar_expression [, offset [, default]]) OVER -> same as input | Value from a previous row.",
  "LEAD(scalar_expression [, offset [, default]]) OVER -> same as input | Value from a following row.",
  "FIRST_VALUE(scalar_expression) OVER -> same as input | First value in the window frame.",
  "LAST_VALUE(scalar_expression) OVER -> same as input | Last value in the window frame.",
  "PERCENT_RANK() OVER -> float | Relative rank of the row (0–1).",
  "CUME_DIST() OVER -> float | Cumulative distribution of the row (0–1).",
];
const AGGREGATES = [
  "COUNT(*) -> int | Number of rows.",
  "COUNT([DISTINCT] expression) -> int | Number of non-NULL values (DISTINCT: unique values).",
  "SUM([DISTINCT] expression) -> numeric | Sum of the values.",
  "AVG([DISTINCT] expression) -> numeric | Average of the values.",
  "MIN(expression) -> same as input | Smallest value.",
  "MAX(expression) -> same as input | Largest value.",
];

const SPECS = {
  mssql: [
    ...AGGREGATES, ...WINDOW,
    "COUNT_BIG(expression) -> bigint | Number of rows/values as bigint.",
    "STRING_AGG(expression, separator) -> nvarchar | Concatenates values with a separator (add WITHIN GROUP (ORDER BY …) to sort).",
    "STDEV(expression) -> float | Sample standard deviation.",
    "VAR(expression) -> float | Sample variance.",
    "GROUPING(column) -> tinyint | 1 if the column is aggregated in a ROLLUP/CUBE row.",
    // date & time
    "GETDATE() -> datetime | Current date and time of the server.",
    "GETUTCDATE() -> datetime | Current UTC date and time.",
    "SYSDATETIME() -> datetime2 | Current date and time (high precision).",
    "SYSUTCDATETIME() -> datetime2 | Current UTC date and time (high precision).",
    "SYSDATETIMEOFFSET() -> datetimeoffset | Current date and time with the server time-zone offset.",
    "DATEADD(datepart:@mssql_datepart, number, date) -> same type as date | Adds a number of dateparts to a date.",
    "DATEDIFF(datepart:@mssql_datepart, startdate, enddate) -> int | Number of datepart boundaries crossed between two dates.",
    "DATEDIFF_BIG(datepart:@mssql_datepart, startdate, enddate) -> bigint | Like DATEDIFF, for very large differences.",
    "DATEPART(datepart:@mssql_datepart_ext, date) -> int | The specified part of a date as an integer.",
    "DATENAME(datepart:@mssql_datepart_ext, date) -> nvarchar | The specified part of a date as text (e.g. 'November').",
    "DATETRUNC(datepart:@mssql_datepart, date) -> same type as date | Truncates a date to the start of the datepart (SQL Server 2022+).",
    "DATE_BUCKET(datepart:@mssql_datepart, number, date [, origin]) -> same type as date | Start of the fixed-size bucket containing the date (SQL Server 2022+).",
    "DATEFROMPARTS(year, month, day) -> date | Builds a date.",
    "DATETIMEFROMPARTS(year, month, day, hour, minute, seconds, milliseconds) -> datetime | Builds a datetime.",
    "DATETIME2FROMPARTS(year, month, day, hour, minute, seconds, fractions, precision) -> datetime2 | Builds a datetime2.",
    "TIMEFROMPARTS(hour, minute, seconds, fractions, precision) -> time | Builds a time.",
    "EOMONTH(start_date [, month_to_add]) -> date | Last day of the month (optionally shifted by months).",
    "YEAR(date) -> int | Year of the date.",
    "MONTH(date) -> int | Month (1–12) of the date.",
    "DAY(date) -> int | Day of the month of the date.",
    "ISDATE(expression) -> int | 1 if the expression is a valid date/time.",
    "SWITCHOFFSET(datetimeoffset_expression, timezoneoffset_expression) -> datetimeoffset | Changes the time-zone offset.",
    "TODATETIMEOFFSET(datetime_expression, timezoneoffset_expression) -> datetimeoffset | Adds a time-zone offset to a datetime.",
    // string
    "LEN(string_expression) -> int | Number of characters (trailing spaces excluded).",
    "DATALENGTH(expression) -> int | Number of bytes used.",
    "SUBSTRING(expression, start, length) -> same type | Part of a string.",
    "LEFT(character_expression, integer_expression) -> varchar | Leftmost characters.",
    "RIGHT(character_expression, integer_expression) -> varchar | Rightmost characters.",
    "LTRIM(character_expression) -> varchar | Removes leading spaces.",
    "RTRIM(character_expression) -> varchar | Removes trailing spaces.",
    "TRIM(string) -> varchar | Removes leading and trailing spaces.",
    "TRIM(characters FROM string) -> varchar | Removes the given characters from both ends (SQL Server 2017+).",
    "UPPER(character_expression) -> varchar | Upper-case text.",
    "LOWER(character_expression) -> varchar | Lower-case text.",
    "REPLACE(string_expression, string_pattern, string_replacement) -> varchar | Replaces every occurrence of a pattern.",
    "REPLICATE(string_expression, integer_expression) -> varchar | Repeats a string.",
    "REVERSE(string_expression) -> varchar | Reverses a string.",
    "CHARINDEX(expression_to_find, expression_to_search [, start_location]) -> int | Position of a substring (0 if not found).",
    "PATINDEX(pattern, expression) -> int | Position of a LIKE pattern, e.g. '%[0-9]%'.",
    "CONCAT(string_value1, string_value2 [, ...string_valueN]) -> varchar | Joins values (NULL treated as empty).",
    "CONCAT_WS(separator, argument1, argument2 [, ...argumentN]) -> varchar | Joins values with a separator, skipping NULLs.",
    "STUFF(character_expression, start, length, replace_with_expression) -> varchar | Deletes part of a string and inserts another.",
    "FORMAT(value, format [, culture]) -> nvarchar | Formats a value with a .NET format string, e.g. 'dd/MM/yyyy', 'N2'.",
    "STRING_SPLIT(string, separator [, enable_ordinal]) -> table | Splits a string into rows (use in FROM / CROSS APPLY).",
    "TRANSLATE(input_string, characters, translations) -> varchar | Replaces characters one-for-one.",
    "QUOTENAME(character_string [, quote_character]) -> nvarchar | Wraps a name in [ ] (safe dynamic SQL).",
    "SPACE(integer_expression) -> varchar | A string of spaces.",
    "STR(float_expression [, length [, decimal]]) -> varchar | Number as text.",
    "ASCII(character_expression) -> int | Code of the first character.",
    "CHAR(integer_expression) -> char | Character for an ASCII code.",
    "UNICODE(ncharacter_expression) -> int | Unicode code point of the first character.",
    "NCHAR(integer_expression) -> nchar | Character for a Unicode code point.",
    "SOUNDEX(character_expression) -> varchar | Four-character phonetic code.",
    "DIFFERENCE(character_expression1, character_expression2) -> int | Similarity of two SOUNDEX codes (0–4).",
    // conversion & logic
    "CAST(expression AS data_type:@mssql_type) -> data_type | Converts a value to another data type.",
    "TRY_CAST(expression AS data_type:@mssql_type) -> data_type | Like CAST, but returns NULL instead of failing.",
    "CONVERT(data_type:@mssql_type, expression [, style:@mssql_style]) -> data_type | Converts a value, with an optional style code for dates/numbers.",
    "TRY_CONVERT(data_type:@mssql_type, expression [, style:@mssql_style]) -> data_type | Like CONVERT, but returns NULL instead of failing.",
    "PARSE(string_value AS data_type:@mssql_type) -> data_type | Parses text using a culture.",
    "PARSE(string_value AS data_type:@mssql_type USING culture) -> data_type | Parses text using the given culture, e.g. 'en-GB'.",
    "TRY_PARSE(string_value AS data_type:@mssql_type) -> data_type | Like PARSE, but returns NULL instead of failing.",
    "ISNULL(check_expression, replacement_value) -> type of check_expression | Replaces NULL with a value.",
    "COALESCE(expression1, expression2 [, ...expressionN]) -> highest-precedence type | First non-NULL value.",
    "NULLIF(expression1, expression2) -> type of expression1 | NULL if both are equal, otherwise the first.",
    "IIF(boolean_expression, true_value, false_value) -> highest-precedence type | Inline IF.",
    "CHOOSE(index, value1, value2 [, ...valueN]) -> highest-precedence type | Value at a 1-based index.",
    "GREATEST(expression1 [, ...expressionN]) -> highest-precedence type | Largest value (SQL Server 2022+).",
    "LEAST(expression1 [, ...expressionN]) -> highest-precedence type | Smallest value (SQL Server 2022+).",
    // math
    "ABS(numeric_expression) -> same type | Absolute value.",
    "CEILING(numeric_expression) -> same type | Smallest integer ≥ the value.",
    "FLOOR(numeric_expression) -> same type | Largest integer ≤ the value.",
    "ROUND(numeric_expression, length [, function]) -> same type | Rounds to length decimals (function ≠ 0 truncates).",
    "POWER(float_expression, y) -> float | Value raised to a power.",
    "SQRT(float_expression) -> float | Square root.",
    "SQUARE(float_expression) -> float | Square.",
    "EXP(float_expression) -> float | e raised to the value.",
    "LOG(float_expression [, base]) -> float | Natural logarithm (or in the given base).",
    "LOG10(float_expression) -> float | Base-10 logarithm.",
    "PI() -> float | π.",
    "RAND([seed]) -> float | Random number between 0 and 1.",
    "SIGN(numeric_expression) -> same type | -1, 0 or 1.",
    // system, errors, JSON
    "NEWID() -> uniqueidentifier | New random GUID.",
    "NEWSEQUENTIALID() -> uniqueidentifier | Sequential GUID (column DEFAULT only).",
    "SCOPE_IDENTITY() -> numeric(38,0) | Last identity value inserted in the current scope.",
    "IDENT_CURRENT(table_or_view) -> numeric(38,0) | Last identity value generated for a table.",
    "OBJECT_ID(object_name [, object_type]) -> int | Object id of a schema-scoped object (NULL if missing).",
    "OBJECT_NAME(object_id [, database_id]) -> sysname | Name of an object id.",
    "OBJECT_DEFINITION(object_id) -> nvarchar(max) | Source of a view, procedure, function or trigger.",
    "DB_NAME([database_id]) -> nvarchar | Current (or given) database name.",
    "DB_ID([database_name]) -> int | Database id.",
    "SCHEMA_NAME([schema_id]) -> sysname | Schema name.",
    "USER_NAME([id]) -> nvarchar | Database user name.",
    "SUSER_SNAME([server_user_sid]) -> nvarchar | Login name.",
    "HOST_NAME() -> nvarchar | Client workstation name.",
    "APP_NAME() -> nvarchar | Client application name.",
    "ROWCOUNT_BIG() -> bigint | Rows affected by the last statement.",
    "ERROR_MESSAGE() -> nvarchar | Message of the error caught by CATCH.",
    "ERROR_NUMBER() -> int | Number of the error caught by CATCH.",
    "ERROR_LINE() -> int | Line of the error caught by CATCH.",
    "ERROR_SEVERITY() -> int | Severity of the error caught by CATCH.",
    "ERROR_STATE() -> int | State of the error caught by CATCH.",
    "ERROR_PROCEDURE() -> nvarchar | Routine where the caught error happened.",
    "XACT_STATE() -> smallint | 1 = committable transaction, -1 = must roll back, 0 = none.",
    "HASHBYTES(algorithm:@mssql_hash, input) -> varbinary | Cryptographic hash of the input.",
    "CHECKSUM(expression [, ...expressionN]) -> int | Hash value for change detection.",
    "ISJSON(expression) -> bit | 1 if the text is valid JSON.",
    "JSON_VALUE(expression, path) -> nvarchar(4000) | Scalar value from JSON, e.g. '$.name'.",
    "JSON_QUERY(expression [, path]) -> nvarchar(max) | Object or array from JSON.",
    "JSON_MODIFY(expression, path, new_value) -> nvarchar(max) | Updates a value in JSON.",
    "OPENJSON(json_expression [, path]) -> table | JSON as rows (use in FROM / CROSS APPLY).",
  ],

  postgresql: [
    ...AGGREGATES, ...WINDOW,
    "NTH_VALUE(value, n) OVER -> same as input | Value of the n-th row of the window frame.",
    "STRING_AGG(expression, delimiter) -> text | Concatenates values with a delimiter (ORDER BY allowed inside).",
    "ARRAY_AGG(expression) -> array | Collects values into an array.",
    "BOOL_AND(expression) -> boolean | True if all values are true.",
    "BOOL_OR(expression) -> boolean | True if any value is true.",
    "JSON_AGG(expression) -> json | Collects values into a JSON array.",
    "JSONB_AGG(expression) -> jsonb | Collects values into a JSONB array.",
    "JSONB_OBJECT_AGG(key, value) -> jsonb | Builds a JSONB object from key/value pairs.",
    "STDDEV(expression) -> numeric | Sample standard deviation.",
    "VARIANCE(expression) -> numeric | Sample variance.",
    "PERCENTILE_CONT(fraction) -> double precision | Continuous percentile — add WITHIN GROUP (ORDER BY …).",
    // date & time
    "NOW() -> timestamptz | Current date and time (start of the transaction).",
    "CLOCK_TIMESTAMP() -> timestamptz | Current date and time (changes during the statement).",
    "DATE_TRUNC(field:@pg_trunc, source) -> timestamp | Truncates to the precision of the field.",
    "DATE_TRUNC(field:@pg_trunc, source, time_zone) -> timestamptz | Truncates in the given time zone.",
    "DATE_PART(field:@pg_field_quoted, source) -> double precision | A part of a date/time, e.g. date_part('year', d).",
    "EXTRACT(field:@pg_field FROM source) -> numeric | A part of a date/time, e.g. EXTRACT(YEAR FROM d).",
    "AGE(timestamp1 [, timestamp2]) -> interval | Difference as years/months/days (from now if one argument).",
    "MAKE_DATE(year, month, day) -> date | Builds a date.",
    "MAKE_TIMESTAMP(year, month, day, hour, min, sec) -> timestamp | Builds a timestamp.",
    "MAKE_INTERVAL([years [, months [, weeks [, days [, hours [, mins [, secs]]]]]]]) -> interval | Builds an interval.",
    "TO_CHAR(value, format:@pg_format) -> text | Formats a date/number as text.",
    "TO_DATE(text, format:@pg_format) -> date | Parses text into a date.",
    "TO_TIMESTAMP(text, format:@pg_format) -> timestamptz | Parses text into a timestamp.",
    "TO_TIMESTAMP(epoch_seconds) -> timestamptz | Converts Unix epoch seconds.",
    "TO_NUMBER(text, format:@pg_format) -> numeric | Parses text into a number.",
    "DATE_BIN(stride, source, origin) -> timestamp | Bins a timestamp into fixed intervals.",
    "GENERATE_SERIES(start, stop [, step]) -> setof | Series of values (use in FROM).",
    "JUSTIFY_DAYS(interval) -> interval | Converts 30-day periods to months.",
    // string
    "LENGTH(string) -> int | Number of characters.",
    "CHAR_LENGTH(string) -> int | Number of characters.",
    "SUBSTRING(string FROM start FOR count) -> text | Part of a string (SQL standard form).",
    "SUBSTRING(string, start, count) -> text | Part of a string.",
    "SUBSTR(string, start [, count]) -> text | Part of a string.",
    "POSITION(substring IN string) -> int | Position of a substring (0 if not found).",
    "STRPOS(string, substring) -> int | Position of a substring (0 if not found).",
    "LEFT(string, n) -> text | First n characters (negative: all but the last |n|).",
    "RIGHT(string, n) -> text | Last n characters.",
    "LPAD(string, length [, fill]) -> text | Pads on the left.",
    "RPAD(string, length [, fill]) -> text | Pads on the right.",
    "TRIM(string) -> text | Removes leading and trailing spaces.",
    "TRIM(characters FROM string) -> text | Removes the given characters (LEADING / TRAILING / BOTH).",
    "BTRIM(string [, characters]) -> text | Removes characters from both ends.",
    "LTRIM(string [, characters]) -> text | Removes leading characters.",
    "RTRIM(string [, characters]) -> text | Removes trailing characters.",
    "UPPER(string) -> text | Upper-case text.",
    "LOWER(string) -> text | Lower-case text.",
    "INITCAP(string) -> text | Capitalises each word.",
    "REPLACE(string, from, to) -> text | Replaces every occurrence of a substring.",
    "TRANSLATE(string, from, to) -> text | Replaces characters one-for-one.",
    "CONCAT(value1 [, ...valueN]) -> text | Joins values (NULLs ignored).",
    "CONCAT_WS(separator, value1 [, ...valueN]) -> text | Joins values with a separator.",
    "SPLIT_PART(string, delimiter, n) -> text | n-th field of a delimited string.",
    "STRING_TO_ARRAY(string, delimiter) -> text[] | Splits text into an array.",
    "REGEXP_REPLACE(string, pattern, replacement [, flags]) -> text | Regular-expression replace ('g' = all).",
    "REGEXP_MATCHES(string, pattern [, flags]) -> setof text[] | Regular-expression matches.",
    "REPEAT(string, number) -> text | Repeats a string.",
    "REVERSE(string) -> text | Reverses a string.",
    "FORMAT(formatstr [, ...formatarg]) -> text | printf-style formatting (%s, %I, %L).",
    "MD5(string) -> text | MD5 hash as hex.",
    "QUOTE_IDENT(string) -> text | Quotes an identifier for dynamic SQL.",
    "QUOTE_LITERAL(string) -> text | Quotes a literal for dynamic SQL.",
    "ASCII(string) -> int | Code of the first character.",
    "CHR(code) -> text | Character for a code.",
    // conversion & logic
    "CAST(expression AS data_type:@pg_type) -> data_type | Converts a value (also: expression::type).",
    "COALESCE(value1, value2 [, ...valueN]) -> common type | First non-NULL value.",
    "NULLIF(value1, value2) -> type of value1 | NULL if both are equal.",
    "GREATEST(value1 [, ...valueN]) -> common type | Largest value.",
    "LEAST(value1 [, ...valueN]) -> common type | Smallest value.",
    // math
    "ABS(x) -> same type | Absolute value.",
    "CEIL(x) -> same type | Smallest integer ≥ x.",
    "FLOOR(x) -> same type | Largest integer ≤ x.",
    "ROUND(x [, digits]) -> numeric | Rounds to the given number of decimals.",
    "TRUNC(x [, digits]) -> numeric | Truncates to the given number of decimals.",
    "POWER(a, b) -> double precision | a raised to b.",
    "SQRT(x) -> double precision | Square root.",
    "MOD(y, x) -> same type | Remainder of y / x.",
    "LN(x) -> double precision | Natural logarithm.",
    "LOG(x) -> double precision | Base-10 logarithm.",
    "LOG(base, x) -> numeric | Logarithm in the given base.",
    "EXP(x) -> double precision | e raised to x.",
    "SIGN(x) -> same type | -1, 0 or 1.",
    "PI() -> double precision | π.",
    "RANDOM() -> double precision | Random number between 0 and 1.",
    // JSON & arrays
    "JSONB_BUILD_OBJECT(key1, value1 [, ...key_value_pairs]) -> jsonb | Builds a JSONB object.",
    "JSON_BUILD_OBJECT(key1, value1 [, ...key_value_pairs]) -> json | Builds a JSON object.",
    "JSONB_EXTRACT_PATH_TEXT(from_json, ...path_elems) -> text | Value at a path as text.",
    "JSONB_SET(target, path, new_value [, create_if_missing]) -> jsonb | Replaces a value at a path, e.g. '{a,b}'.",
    "JSONB_ARRAY_ELEMENTS(from_json) -> setof jsonb | Expands a JSON array to rows.",
    "JSONB_EACH(from_json) -> setof (key, value) | Expands a JSON object to key/value rows.",
    "JSONB_TYPEOF(from_json) -> text | Type of the JSON value.",
    "TO_JSONB(value) -> jsonb | Converts any value to JSONB.",
    "ARRAY_LENGTH(array, dimension) -> int | Length of an array dimension.",
    "CARDINALITY(array) -> int | Total number of elements.",
    "UNNEST(array) -> setof | Expands an array to rows.",
    "ARRAY_TO_STRING(array, delimiter [, null_string]) -> text | Joins array elements.",
    "ARRAY_APPEND(array, element) -> array | Appends an element.",
    // system
    "GEN_RANDOM_UUID() -> uuid | New random UUID.",
    "CURRENT_DATABASE() -> name | Current database name.",
    "CURRENT_SCHEMA() -> name | First schema in the search path.",
    "VERSION() -> text | Server version string.",
    "NEXTVAL(sequence) -> bigint | Advances a sequence, e.g. nextval('orders_id_seq').",
    "CURRVAL(sequence) -> bigint | Last value from nextval in this session.",
    "SETVAL(sequence, value [, is_called]) -> bigint | Sets a sequence's value.",
    "PG_SIZE_PRETTY(bytes) -> text | Human-readable size, e.g. '12 MB'.",
    "PG_TOTAL_RELATION_SIZE(relation) -> bigint | Size of a table incl. indexes and TOAST.",
    "PG_SLEEP(seconds) -> void | Pauses for the given seconds.",
  ],

  mysql: [
    ...AGGREGATES, ...WINDOW,
    "NTH_VALUE(expr, n) OVER -> same as input | Value of the n-th row of the window frame.",
    "GROUP_CONCAT([DISTINCT] expr) -> text | Concatenates values (ORDER BY … SEPARATOR ',' allowed inside).",
    "JSON_ARRAYAGG(expr) -> json | Collects values into a JSON array.",
    "JSON_OBJECTAGG(key, value) -> json | Builds a JSON object from key/value pairs.",
    "STD(expr) -> double | Population standard deviation.",
    "VARIANCE(expr) -> double | Population variance.",
    // date & time
    "NOW([fsp]) -> datetime | Current date and time.",
    "CURDATE() -> date | Current date.",
    "CURTIME([fsp]) -> time | Current time.",
    "SYSDATE([fsp]) -> datetime | Time the function executes.",
    "UTC_TIMESTAMP([fsp]) -> datetime | Current UTC date and time.",
    "DATE_ADD(date, INTERVAL value unit) -> date/datetime | Adds an interval, e.g. DATE_ADD(d, INTERVAL 7 DAY).",
    "DATE_SUB(date, INTERVAL value unit) -> date/datetime | Subtracts an interval.",
    "ADDDATE(date, days) -> date | Adds days.",
    "DATEDIFF(expr1, expr2) -> int | Days between two dates (expr1 − expr2).",
    "TIMESTAMPDIFF(unit:@my_unit, datetime_expr1, datetime_expr2) -> bigint | Difference in the given unit.",
    "TIMESTAMPADD(unit:@my_unit, interval, datetime_expr) -> datetime | Adds an interval in the given unit.",
    "DATE_FORMAT(date, format:@my_format) -> varchar | Formats a date, e.g. '%Y-%m-%d'.",
    "STR_TO_DATE(str, format:@my_format) -> datetime | Parses text into a date.",
    "EXTRACT(unit:@my_unit FROM date) -> bigint | Part of a date, e.g. EXTRACT(YEAR FROM d).",
    "YEAR(date) -> int | Year.",
    "MONTH(date) -> int | Month (1–12).",
    "DAY(date) -> int | Day of the month.",
    "DAYOFWEEK(date) -> int | Weekday (1 = Sunday).",
    "DAYNAME(date) -> varchar | Weekday name.",
    "MONTHNAME(date) -> varchar | Month name.",
    "WEEK(date [, mode]) -> int | Week number.",
    "LAST_DAY(date) -> date | Last day of the month.",
    "DATE(expr) -> date | Date part of a datetime.",
    "TIME(expr) -> time | Time part of a datetime.",
    "UNIX_TIMESTAMP([date]) -> bigint | Seconds since 1970.",
    "FROM_UNIXTIME(unix_timestamp [, format:@my_format]) -> datetime | Datetime from Unix seconds.",
    "MAKEDATE(year, dayofyear) -> date | Builds a date.",
    "CONVERT_TZ(dt, from_tz, to_tz) -> datetime | Converts between time zones, e.g. '+00:00'.",
    // string
    "LENGTH(str) -> int | Length in bytes.",
    "CHAR_LENGTH(str) -> int | Length in characters.",
    "CONCAT(str1, str2 [, ...strN]) -> varchar | Joins strings (NULL if any is NULL).",
    "CONCAT_WS(separator, str1, str2 [, ...strN]) -> varchar | Joins strings with a separator, skipping NULLs.",
    "SUBSTRING(str, pos [, len]) -> varchar | Part of a string.",
    "SUBSTRING(str FROM pos FOR len) -> varchar | Part of a string (SQL standard form).",
    "SUBSTRING_INDEX(str, delim, count) -> varchar | Text before the count-th delimiter (negative: after).",
    "LEFT(str, len) -> varchar | Leftmost characters.",
    "RIGHT(str, len) -> varchar | Rightmost characters.",
    "LPAD(str, len, padstr) -> varchar | Pads on the left.",
    "RPAD(str, len, padstr) -> varchar | Pads on the right.",
    "TRIM(str) -> varchar | Removes leading and trailing spaces.",
    "TRIM(remstr FROM str) -> varchar | Removes the given string (LEADING / TRAILING / BOTH).",
    "LTRIM(str) -> varchar | Removes leading spaces.",
    "RTRIM(str) -> varchar | Removes trailing spaces.",
    "UPPER(str) -> varchar | Upper-case text.",
    "LOWER(str) -> varchar | Lower-case text.",
    "REPLACE(str, from_str, to_str) -> varchar | Replaces every occurrence.",
    "INSTR(str, substr) -> int | Position of a substring (0 if not found).",
    "LOCATE(substr, str [, pos]) -> int | Position of a substring from pos.",
    "REVERSE(str) -> varchar | Reverses a string.",
    "REPEAT(str, count) -> varchar | Repeats a string.",
    "FORMAT(x, d [, locale]) -> varchar | Number with d decimals and thousands separators.",
    "FIND_IN_SET(str, strlist) -> int | Position in a comma-separated list.",
    "FIELD(str, str1, str2 [, ...strN]) -> int | Index of str in the list.",
    "ELT(n, str1 [, ...strN]) -> varchar | n-th string of the list.",
    "REGEXP_REPLACE(expr, pat, repl) -> varchar | Regular-expression replace.",
    "REGEXP_LIKE(expr, pat [, match_type]) -> int | 1 if the expression matches.",
    "MD5(str) -> varchar | MD5 hash as hex.",
    "SHA2(str, hash_length) -> varchar | SHA-2 hash (224, 256, 384 or 512).",
    "HEX(str) -> varchar | Hexadecimal representation.",
    "ASCII(str) -> int | Code of the first character.",
    // conversion & logic
    "CAST(expr AS type:@my_type) -> type | Converts a value.",
    "CONVERT(expr, type:@my_type) -> type | Converts a value.",
    "CONVERT(expr USING transcoding_name:@my_charset) -> string | Converts between character sets.",
    "IFNULL(expr1, expr2) -> common type | expr2 if expr1 is NULL.",
    "NULLIF(expr1, expr2) -> type of expr1 | NULL if both are equal.",
    "COALESCE(value1, value2 [, ...valueN]) -> common type | First non-NULL value.",
    "IF(expr1, expr2, expr3) -> common type | expr2 if expr1 is true, else expr3.",
    "GREATEST(value1, value2 [, ...valueN]) -> common type | Largest value.",
    "LEAST(value1, value2 [, ...valueN]) -> common type | Smallest value.",
    // math
    "ABS(x) -> same type | Absolute value.",
    "CEIL(x) -> bigint | Smallest integer ≥ x.",
    "FLOOR(x) -> bigint | Largest integer ≤ x.",
    "ROUND(x [, d]) -> same type | Rounds to d decimals.",
    "TRUNCATE(x, d) -> same type | Truncates to d decimals.",
    "MOD(n, m) -> same type | Remainder of n / m.",
    "POW(x, y) -> double | x raised to y.",
    "SQRT(x) -> double | Square root.",
    "RAND([seed]) -> double | Random number between 0 and 1.",
    "SIGN(x) -> int | -1, 0 or 1.",
    "PI() -> double | π.",
    "LOG(x) -> double | Natural logarithm.",
    "LOG(b, x) -> double | Logarithm of x in base b.",
    "EXP(x) -> double | e raised to x.",
    // JSON & system
    "JSON_EXTRACT(json_doc, path [, ...path]) -> json | Value at a path, e.g. '$.name' (also col->'$.name').",
    "JSON_UNQUOTE(json_val) -> text | Removes JSON quoting (also col->>'$.name').",
    "JSON_OBJECT(key, val [, ...key_val]) -> json | Builds a JSON object.",
    "JSON_ARRAY(val [, ...val]) -> json | Builds a JSON array.",
    "JSON_SET(json_doc, path, val [, ...path_val]) -> json | Inserts or replaces values.",
    "JSON_CONTAINS(target, candidate [, path]) -> int | 1 if the candidate is contained.",
    "JSON_LENGTH(json_doc [, path]) -> int | Number of elements.",
    "JSON_VALID(val) -> int | 1 if valid JSON.",
    "LAST_INSERT_ID([expr]) -> bigint | Last AUTO_INCREMENT value generated in this session.",
    "ROW_COUNT() -> bigint | Rows affected by the previous statement.",
    "DATABASE() -> varchar | Current database.",
    "USER() -> varchar | Current user and host.",
    "VERSION() -> varchar | Server version.",
    "CONNECTION_ID() -> bigint | Connection (thread) id.",
    "UUID() -> varchar | New UUID.",
    "SLEEP(duration) -> int | Pauses for the given seconds.",
  ],

  sqlite: [
    ...AGGREGATES, ...WINDOW,
    "NTH_VALUE(expr, n) OVER -> same as input | Value of the n-th row of the window frame.",
    "TOTAL(x) -> real | Sum as a floating-point value (0.0 for no rows).",
    "GROUP_CONCAT(x [, separator]) -> text | Concatenates values (default separator ',').",
    "STRING_AGG(x, separator) -> text | Concatenates values with a separator (SQLite 3.44+).",
    "JSON_GROUP_ARRAY(x) -> text | Collects values into a JSON array.",
    "JSON_GROUP_OBJECT(name, value) -> text | Builds a JSON object from name/value pairs.",
    // date & time
    "DATE(time_value [, ...modifier:@sqlite_modifier]) -> text | Date as YYYY-MM-DD, e.g. date('now', 'start of month').",
    "TIME(time_value [, ...modifier:@sqlite_modifier]) -> text | Time as HH:MM:SS.",
    "DATETIME(time_value [, ...modifier:@sqlite_modifier]) -> text | Date and time as YYYY-MM-DD HH:MM:SS.",
    "JULIANDAY(time_value [, ...modifier:@sqlite_modifier]) -> real | Julian day number.",
    "UNIXEPOCH(time_value [, ...modifier:@sqlite_modifier]) -> integer | Seconds since 1970 (SQLite 3.38+).",
    "STRFTIME(format:@sqlite_format, time_value [, ...modifier:@sqlite_modifier]) -> text | Formats a date/time.",
    "TIMEDIFF(time1, time2) -> text | Difference as '+YYYY-MM-DD HH:MM:SS' (SQLite 3.43+).",
    // string
    "LENGTH(x) -> integer | Number of characters (bytes for BLOBs).",
    "SUBSTR(x, start [, length]) -> text | Part of a string.",
    "SUBSTRING(x, start [, length]) -> text | Part of a string.",
    "INSTR(x, y) -> integer | Position of y in x (0 if not found).",
    "REPLACE(x, y, z) -> text | Replaces every y in x with z.",
    "UPPER(x) -> text | Upper-case text (ASCII).",
    "LOWER(x) -> text | Lower-case text (ASCII).",
    "TRIM(x [, characters]) -> text | Removes characters from both ends.",
    "LTRIM(x [, characters]) -> text | Removes characters from the start.",
    "RTRIM(x [, characters]) -> text | Removes characters from the end.",
    "PRINTF(format [, ...args]) -> text | printf-style formatting, e.g. printf('%.2f', x).",
    "FORMAT(format [, ...args]) -> text | Same as PRINTF.",
    "CONCAT(x [, ...x]) -> text | Joins values (SQLite 3.44+).",
    "CONCAT_WS(separator, x [, ...x]) -> text | Joins values with a separator (SQLite 3.44+).",
    "HEX(x) -> text | Hexadecimal representation.",
    "QUOTE(x) -> text | SQL literal for the value.",
    "CHAR(x1 [, ...xN]) -> text | String from Unicode code points.",
    "UNICODE(x) -> integer | Code point of the first character.",
    "LIKE(pattern, x [, escape]) -> integer | Function form of x LIKE pattern.",
    "GLOB(pattern, x) -> integer | Case-sensitive Unix-style pattern match.",
    // conversion & logic
    "CAST(expr AS type:@sqlite_type) -> type | Converts a value.",
    "IFNULL(x, y) -> common | y if x is NULL.",
    "NULLIF(x, y) -> type of x | NULL if both are equal.",
    "COALESCE(x, y [, ...z]) -> common | First non-NULL value.",
    "IIF(condition, true_value, false_value) -> common | Inline IF.",
    "TYPEOF(x) -> text | 'null', 'integer', 'real', 'text' or 'blob'.",
    // math
    "ABS(x) -> same | Absolute value.",
    "ROUND(x [, digits]) -> real | Rounds to the given decimals.",
    "RANDOM() -> integer | Random 64-bit integer.",
    "RANDOMBLOB(n) -> blob | n random bytes.",
    "MAX(x, y [, ...z]) -> common | Largest argument (scalar form).",
    "MIN(x, y [, ...z]) -> common | Smallest argument (scalar form).",
    "CEIL(x) -> same | Smallest integer ≥ x.",
    "FLOOR(x) -> same | Largest integer ≤ x.",
    "POWER(x, y) -> real | x raised to y.",
    "SQRT(x) -> real | Square root.",
    "MOD(x, y) -> same | Remainder of x / y.",
    "LN(x) -> real | Natural logarithm.",
    "LOG(x) -> real | Base-10 logarithm.",
    "LOG(b, x) -> real | Logarithm of x in base b.",
    "EXP(x) -> real | e raised to x.",
    "PI() -> real | π.",
    // JSON & system
    "JSON(x) -> text | Validates and minifies JSON.",
    "JSON_EXTRACT(x, path [, ...path]) -> any | Value at a path, e.g. '$.name' (also x->>'$.name').",
    "JSON_OBJECT(label, value [, ...label_value]) -> text | Builds a JSON object.",
    "JSON_ARRAY(value [, ...value]) -> text | Builds a JSON array.",
    "JSON_SET(json, path, value [, ...path_value]) -> text | Inserts or replaces values.",
    "JSON_ARRAY_LENGTH(json [, path]) -> integer | Number of array elements.",
    "JSON_TYPE(json [, path]) -> text | Type of the JSON value.",
    "JSON_VALID(json) -> integer | 1 if valid JSON.",
    "JSON_EACH(json [, path]) -> table | JSON as rows (use in FROM).",
    "LAST_INSERT_ROWID() -> integer | Rowid of the last successful INSERT.",
    "CHANGES() -> integer | Rows changed by the last statement.",
    "TOTAL_CHANGES() -> integer | Rows changed since the connection opened.",
    "SQLITE_VERSION() -> text | SQLite library version.",
  ],
};

// --- spec parser ---------------------------------------------------------------------------------

const KW_SEPARATORS = ["AS", "FROM", "FOR", "USING", "IN", "PLACING"];
const SEP_RE = new RegExp(`\\s+(${KW_SEPARATORS.join("|")})\\s+`, "y");

/**
 * Parse one spec into a signature:
 * { name, label, params:[{name,label,optional,variadic,enumRef,start,end}], separators:Set,
 *   returns, doc, window }
 */
export function parseSpec(spec) {
  let rest = spec;
  let doc = "";
  const bar = rest.indexOf(" | ");
  if (bar !== -1) {
    doc = rest.slice(bar + 3).trim();
    rest = rest.slice(0, bar);
  }
  let returns = "";
  const arrow = rest.indexOf(" -> ");
  if (arrow !== -1) {
    returns = rest.slice(arrow + 4).trim();
    rest = rest.slice(0, arrow);
  }
  let head = rest.trim();
  let window = false;
  if (/\)\s+OVER$/.test(head)) {
    window = true;
    head = head.replace(/\s+OVER$/, "");
  }
  const open = head.indexOf("(");
  const name = head.slice(0, open).trim();
  const inner = head.slice(open + 1, head.lastIndexOf(")"));

  // Split the parameter list on commas / keyword separators at paren depth 0.
  const segments = []; // { raw, sep } — sep = separator that PRECEDES the segment
  const separators = new Set();
  let depth = 0;
  let segStart = 0;
  let pendingSep = null;
  for (let i = 0; i < inner.length; i++) {
    const ch = inner[i];
    if (ch === "(") depth++;
    else if (ch === ")") depth--;
    if (depth !== 0) continue;
    if (ch === ",") {
      segments.push({ raw: inner.slice(segStart, i), sep: pendingSep });
      pendingSep = ",";
      separators.add(",");
      segStart = i + 1;
      continue;
    }
    SEP_RE.lastIndex = i;
    const m = SEP_RE.exec(inner);
    if (m) {
      segments.push({ raw: inner.slice(segStart, i), sep: pendingSep });
      pendingSep = m[1].toLowerCase();
      separators.add(pendingSep);
      segStart = i + m[0].length;
      i = segStart - 1;
    }
  }
  if (inner.trim()) segments.push({ raw: inner.slice(segStart), sep: pendingSep });

  // Strip enum annotations for display, then locate every parameter in the display label.
  const displayInner = inner.replace(/:@\w+/g, "");
  const label = `${name}(${displayInner})`;
  let bracketDepth = 0;
  let cursor = name.length + 1;
  const params = segments.map((seg) => {
    // A leading closed group is a modifier of this parameter ("[DISTINCT] expression"), not
    // an optional parameter; strip it before deciding optionality and finding the text.
    const raw = seg.raw.replace(/^\s*\[[^[\]]*\]\s+(?=\S)/, "");
    // Optional if we're already inside […] or this parameter opens its own group "[, x]".
    // A "[" at the END of the segment ("expr [, offset …") opens the group for the NEXT ones.
    const optional = bracketDepth > 0 || /^\s*\[/.test(raw);
    bracketDepth += (raw.match(/\[/g) || []).length - (raw.match(/]/g) || []).length;
    const core = raw.replace(/[[\]]/g, "").trim();
    const enumMatch = core.match(/:@(\w+)$/);
    const text = core.replace(/:@\w+$/, "");
    const variadic = text.startsWith("...");
    const pname = text.replace(/^\.\.\./, "").replace(/^(DISTINCT|ALL)\s+/i, "");
    const at = label.indexOf(text, cursor);
    const start = at === -1 ? cursor : at;
    const end = start + text.length;
    cursor = end;
    return { name: pname, label: text, optional, variadic, enumRef: enumMatch?.[1] || null, start, end, sep: seg.sep };
  });
  return { name, label, params, separators, returns, doc, window };
}

const _cache = new Map();

/** Map NAME → [signature, …] for a dialect (parsed once). */
export function builtinFunctions(dialect) {
  const key = SPECS[dialect] ? dialect : "postgresql";
  if (!_cache.has(key)) {
    const map = new Map();
    for (const spec of SPECS[key]) {
      const sig = parseSpec(spec);
      if (!map.has(sig.name)) map.set(sig.name, []);
      map.get(sig.name).push(sig);
    }
    _cache.set(key, map);
  }
  return _cache.get(key);
}

export function enumValues(ref) {
  const def = ENUMS[ref];
  if (!def) return null;
  const base = def.ref ? ENUMS[def.ref] : def;
  return { quote: !!def.quote, values: base.values, choices: def.choices || base.choices || null };
}

// Escape text for a snippet choice (${1|a,b|}): backslash, comma, pipe, $ and } are special.
const choiceEscape = (v) => String(v).replace(/[\\,|$}]/g, (c) => "\\" + c);

/** Snippet placeholder for one parameter: a dropdown of its allowed values, or its name. */
function placeholder(n, p) {
  const ev = p.enumRef ? enumValues(p.enumRef) : null;
  if (ev) {
    const list = (ev.choices || ev.values.map(([v]) => v)).slice(0, 25);
    const shown = list.map((v) => choiceEscape(ev.quote ? `'${v}'` : v));
    return "${" + n + "|" + shown.join(",") + "|}";
  }
  return "${" + n + ":" + p.name.replace(/[}$\\]/g, "") + "}";
}

export function paramDoc(param) {
  if (param.doc) return param.doc; // user routines: described from their declaration
  const key = param.name.toLowerCase().replace(/\d+$/, "");
  const ev = param.enumRef ? enumValues(param.enumRef) : null;
  const generic = PARAM_DOCS[param.name.toLowerCase()] || PARAM_DOCS[key] || "";
  if (!ev) return generic;
  const sample = ev.values.slice(0, 8).map(([v]) => (ev.quote ? `'${v}'` : v)).join(", ");
  return `${generic ? `${generic} ` : ""}One of: ${sample}${ev.values.length > 8 ? ", …" : ""}`;
}

/**
 * Snippet for inserting a function: named placeholders for the REQUIRED parameters
 * (optional ones are left out, like SSMS), plus OVER (…) for window functions.
 */
export function functionSnippet(sig) {
  let n = 0;
  let body = "";
  for (const p of sig.params) {
    if (p.optional) break;
    const sep = p.sep === null ? "" : p.sep === "," ? ", " : ` ${p.sep.toUpperCase()} `;
    body += sep + placeholder(++n, p);
  }
  const over = sig.window ? ` OVER (\${${++n}:ORDER BY })` : "";
  return `${sig.name}(${body})${over}`;
}

// --- call context --------------------------------------------------------------------------------

const MAX_SCAN_TOKENS = 4000;
const NOT_CALLABLE = new Set([
  "in", "values", "as", "over", "exists", "and", "or", "not", "on", "from", "join", "where", "select",
  "into", "using", "table", "when", "then", "else", "case", "by", "with", "returns",
]);

/**
 * Find the function call enclosing `offset`.
 * @returns {null | {name:string, schema:string|null, separators:string[], argStart:number,
 *           inString:boolean, procedureCall?:boolean}}
 *   `separators` are the commas / keyword separators already typed at the call's top level.
 */
export function callContext(text, offset, dialect = "postgresql") {
  const { start } = statementBounds(text, offset, dialect);
  const stmt = text.slice(start, offset);
  const all = tokenize(stmt, dialect);
  const last = all[all.length - 1];
  // Cursor inside a string literal that is still open at the cursor (`'da|`)?
  const inString = !!last && last.type === "str" && last.end >= stmt.length &&
    (last.text.length === 1 || !last.text.endsWith("'"));
  const tokens = all.length > MAX_SCAN_TOKENS ? all.slice(-MAX_SCAN_TOKENS) : all;

  let depth = 0;
  const seps = [];
  for (let i = tokens.length - 1; i >= 0; i--) {
    const t = tokens[i];
    if (t.type === "punct" && t.text === ")") { depth++; continue; }
    if (t.type === "punct" && t.text === "(") {
      if (depth > 0) { depth--; continue; }
      const fn = tokens[i - 1];
      if (!fn || fn.type !== "ident" || (!fn.quoted && NOT_CALLABLE.has(fn.lower))) return null;
      let schema = null;
      if (tokens[i - 2]?.text === "." && tokens[i - 3]?.type === "ident") schema = tokens[i - 3].value;
      return {
        name: fn.value,
        schema,
        separators: seps.reverse(),
        argStart: start + t.end,
        inString,
      };
    }
    if (depth !== 0) continue;
    if (t.type === "punct" && t.text === ",") seps.push(",");
    else if (t.type === "punct" && t.text === ";") return null;
    else if (t.type === "ident" && !t.quoted && KW_SEPARATORS.includes(t.value.toUpperCase())) {
      seps.push(t.lower);
    }
  }
  // T-SQL `EXEC [@rc =] [schema.]proc arg1, arg2` — procedure arguments without parentheses.
  if (dialect === "mssql") {
    let k = 0;
    if (tokens[k]?.lower === "exec" || tokens[k]?.lower === "execute") {
      k++;
      if (tokens[k]?.value?.startsWith("@") && tokens[k + 1]?.text === "=") k += 2;
      const nameTok = [];
      while (tokens[k]?.type === "ident") {
        nameTok.push(tokens[k]);
        if (tokens[k + 1]?.text === ".") k += 2;
        else { k++; break; }
      }
      if (nameTok.length && tokens.length > k - 1 && (stmt.length > nameTok[nameTok.length - 1].end)) {
        const commas = tokens.slice(k).filter((t) => t.type === "punct" && t.text === ",").map(() => ",");
        return {
          name: nameTok[nameTok.length - 1].value,
          schema: nameTok.length > 1 ? nameTok[nameTok.length - 2].value : null,
          separators: commas,
          argStart: start + nameTok[nameTok.length - 1].end,
          inString,
          procedureCall: true,
        };
      }
    }
  }
  return null;
}

/** Index of the active parameter for a signature, given the separators typed so far. */
export function activeParamIndex(sig, separators) {
  let idx = 0;
  for (const s of separators) if (sig.separators.has(s) || (s === "," && sig.separators.size === 0)) idx++;
  const last = sig.params.length - 1;
  if (last >= 0 && sig.params[last].variadic && idx > last) return last;
  return idx;
}

/**
 * Choose the best overload and active parameter for a call.
 * @returns {{signatures:object[], activeSignature:number, activeParameter:number} | null}
 */
export function signatureHelp(call, signatures) {
  if (!call || !signatures?.length) return null;
  // Keyword separators typed so far must belong to the overload (TRIM(chars FROM str) vs TRIM(str)).
  const kws = call.separators.filter((s) => s !== ",");
  let best = 0;
  let bestScore = -Infinity;
  signatures.forEach((sig, i) => {
    const idx = activeParamIndex(sig, call.separators);
    const fitsKw = kws.every((k) => sig.separators.has(k));
    const fitsCount = idx < sig.params.length || (sig.params.at(-1)?.variadic ?? false);
    const score = (fitsKw ? 2 : 0) + (fitsCount ? 1 : 0) - i * 0.01;
    if (score > bestScore) { bestScore = score; best = i; }
  });
  const sig = signatures[best];
  return { signatures, activeSignature: best, activeParameter: Math.min(activeParamIndex(sig, call.separators), Math.max(sig.params.length - 1, 0)) };
}

/**
 * Allowed values for the parameter at the cursor (e.g. dateparts inside DATEADD( … ).
 * Returns null when the active parameter is free-form. Inside an open string literal the
 * values are returned without quotes (the user already typed the opening quote).
 */
export function parameterValueItems(call, dialect) {
  if (!call) return null;
  const sigs = builtinFunctions(dialect).get(String(call.name).toUpperCase());
  const help = signatureHelp(call, sigs);
  if (!help) return null;
  const sig = help.signatures[help.activeSignature];
  const param = sig.params[help.activeParameter];
  const ev = param?.enumRef ? enumValues(param.enumRef) : null;
  if (!ev) return null;
  // Quoted values are only offered inside a string or at a fresh argument; bare values never
  // inside a string.
  if (call.inString && !ev.quote) return null;
  const wrap = ev.quote && !call.inString;
  return {
    sig,
    param,
    items: ev.values.map(([value, doc], i) => ({
      label: wrap ? `'${value}'` : value,
      insertText: wrap ? `'${value}'` : value,
      detail: doc,
      sortText: String(i).padStart(3, "0"),
    })),
  };
}

const PARAM_MODES = /^(IN|OUT|INOUT|VARIADIC)\s+/i;

function paramName(label) {
  return String(label).replace(PARAM_MODES, "").split(/\s+/)[0];
}

/** "@Count int OUTPUT" → "Type: int · output parameter (returns a value)". */
function describeRoutineParam(label) {
  let text = String(label).trim();
  const mode = (text.match(PARAM_MODES)?.[1] || "").toUpperCase();
  text = text.replace(PARAM_MODES, "");
  const output = /\s+OUTPUT$/i.test(text) || mode === "OUT" || mode === "INOUT";
  text = text.replace(/\s+OUTPUT$/i, "");
  const def = text.match(/\s+DEFAULT\s+(.+)$/i);
  if (def) text = text.slice(0, def.index);
  const type = text.split(/\s+/).slice(1).join(" ");
  const parts = [type ? `Type: ${type}` : ""];
  if (output) parts.push(mode === "INOUT" ? "input/output parameter" : "output parameter (returns a value)");
  if (def) parts.push(`default ${def[1]}`);
  return parts.filter(Boolean).join(" · ");
}

/**
 * Signatures for a user-defined routine from the catalog ({name, kind, params:[label], returns}).
 * Procedure calls (EXEC) separate arguments with commas only.
 */
export function routineSignature(routine, procedureCall = false) {
  const params = Array.isArray(routine.params) ? routine.params : [];
  const name = routine.schema_name ? `${routine.schema_name}.${routine.name}` : routine.name;
  const open = procedureCall ? " " : "(";
  let label = name + open;
  const out = params.map((p, i) => {
    if (i) label += ", ";
    const startPos = label.length;
    label += p;
    return {
      name: paramName(p), label: p, doc: describeRoutineParam(p), optional: false, variadic: false,
      enumRef: null, start: startPos, end: label.length, sep: i ? "," : null,
    };
  });
  if (!procedureCall) label += ")";
  const kind = routine.kind === "procedure" ? "Stored procedure" : "Function";
  return {
    name: routine.name,
    label,
    params: out,
    separators: new Set([","]),
    returns: routine.returns || "",
    doc: `${kind}${routine.schema_name ? ` in schema ${routine.schema_name}` : ""}${params.length ? "" : " · no parameters"}`,
    window: false,
    userDefined: true,
  };
}
