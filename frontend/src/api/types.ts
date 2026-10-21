// The API's shapes, taken from the contract (docs/openapi.yaml) through the generated
// schema.gen.ts. Don't describe API shapes by hand here: change the API, regenerate the
// contract (python manage.py api_contract --write) and the types (npm run api:types).
// This file only gives the generated types short names for the rest of the panel.
import type { components } from "./schema.gen";

type Schemas = components["schemas"];

// -- status ----------------------------------------------------------------------------------------
export type Fix = Schemas["Fix"];
export type Problem = Schemas["Problem"];
export type CurrentProblem = Schemas["CurrentProblem"];
export type BotState = Schemas["BotState"];
export type BotStatus = Schemas["Status"];
export type LoginState = Schemas["LoginState"];
export type BlueskyLogin = Schemas["BlueskyLogin"];
export type LastCheck = Schemas["LastCheck"];
export type SchedulerHealth = Schemas["SchedulerHealth"];
export type Health = Schemas["Health"];

// -- accounts --------------------------------------------------------------------------------------
export type AccountState = Schemas["AccountState"];
export type Account = Schemas["Account"];
/** A new account, or the fields to change on one (only `handle` is required when adding). */
export type AccountInput = Schemas["PatchedAccountRequest"];
export type PollResult = Schemas["PollResult"];
export type Post = Schemas["Post"];

// -- channels --------------------------------------------------------------------------------------
export type Channel = Schemas["Channel"];
export type ChannelInput = Schemas["PatchedChannelRequest"];

// -- settings --------------------------------------------------------------------------------------
export type Settings = Schemas["Settings"];
export type SettingsInput = Schemas["PatchedSettingsChangeRequest"];
export type LoginTestResult = Schemas["LoginTestResult"];
export type LoginTest = Schemas["LoginTest"];

// -- Bluesky profiles ------------------------------------------------------------------------------
export type LookupResult = Schemas["LookupResult"];
export type ProfileLookup = Schemas["ProfileLookup"];

// -- activity --------------------------------------------------------------------------------------
export type ActivityLevel = Schemas["ActivityLevel"];
export type ActivityEvent = Schemas["ActivityEvent"];

// -- Check All jobs --------------------------------------------------------------------------------
export type JobStatus = Schemas["JobStatus"];
export type JobAccountResult = Schemas["JobAccountResult"];
/** A job; `created` is only in the answer to POST /api/accounts/check_all/. */
export type CheckJob = Schemas["CheckJob"] & Partial<Pick<Schemas["CheckJobStarted"], "created">>;

// -- paging ----------------------------------------------------------------------------------------
/** Page-numbered lists (accounts, channels, posts): the same shape as Paginated*List in the contract. */
export type Paged<T> = Omit<Schemas["PaginatedAccountList"], "results"> & { results: T[] };
/** The cursor-paged activity log: the same shape as PaginatedActivityEventList. */
export type CursorPage<T> = Omit<Schemas["PaginatedActivityEventList"], "results"> & { results: T[] };
