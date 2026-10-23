/**
 * Apps Script clock + sheet-change dispatcher (container-bound to the book).
 *
 * This script is a THIN dispatcher. It never decides schedules, catch-up,
 * dedup, freshness or writes: scheduler.yml and config_refresh.yml remain the
 * only decision makers. The clock reads the LIVE `ajustes` tab only to know
 * WHEN to dispatch; the scheduler still reads the committed snapshot.
 *
 * Primary paths:
 *   clockTick (every 5 min) -> scheduler.yml when a sheet window starts
 *   onSheetChange (EDIT)    -> config_refresh.yml (sync + form write)
 * GitHub schedule (0 * /6) remains as a 6 h safety net.
 *
 * Required ScriptProperties (Project Settings -> Script properties):
 *   GH_PAT                        fine-grained PAT, only this repo, Actions: write
 *   APPSCRIPT_HEALTHCHECKS_URL    full ping URL of the `appscript-clock` check
 *   SCHEDULER_DISPATCH_URL        optional (default below)
 *   CONFIG_REFRESH_DISPATCH_URL   optional (default below)
 *   SHEET_ID                      optional (default: the container-bound book)
 *
 * Security: the PAT lives in ScriptProperties and is readable by anyone who
 * can edit this Apps Script project. Rotate it when collaborators change and
 * never paste it into this file or the repository.
 *
 * Installation: Extensions -> Apps Script, paste this file, save, run
 * setupTriggers() once, authorize the scopes, then run testDispatch() and/or
 * clockTick() manually. If this file changes in the repo, paste it again in the
 * editor; the triggers stay installed (setupTriggers is idempotent).
 */

var REPO = "erdesarr/economic-indicators";
var TZ = "America/Bogota";
var WINDOW_MINUTES = 15;
var REFRESH_THROTTLE_MS = 5 * 60 * 1000;
var DEFAULT_SCHEDULER_DISPATCH_URL =
  "https://api.github.com/repos/" + REPO + "/actions/workflows/scheduler.yml/dispatches";
var DEFAULT_CONFIG_REFRESH_DISPATCH_URL =
  "https://api.github.com/repos/" + REPO + "/actions/workflows/config_refresh.yml/dispatches";

function clockTick() {
  var errors = [];
  var dueLabels = [];
  var dispatchedLabels = [];
  try {
    var now = new Date();
    var today = Utilities.formatDate(now, TZ, "yyyy-MM-dd");
    var nowMinutes =
      Number(Utilities.formatDate(now, TZ, "HH")) * 60 +
      Number(Utilities.formatDate(now, TZ, "mm"));
    var settings = readAjustes_();
    var dispatched = loadDispatched_(today);
    var due = [];

    ["horario_etl", "horario_updater"].forEach(function (key) {
      var parsed = parseDailyCron_(settings[key]);
      if (!parsed) {
        Logger.log("clock: missing/unsupported schedule for " + key + ": " + settings[key]);
        return;
      }
      if (!withinWindow_(nowMinutes, parsed.hour * 60 + parsed.minute)) {
        return;
      }
      var token = today + "|" + parsed.label;
      dueLabels.push(parsed.label + "(" + key + ")");
      if (!dispatched[token]) {
        due.push({ key: key, label: parsed.label, token: token });
      }
    });

    if (due.length > 0) {
      var result = dispatch_(schedulerUrl_(), { ref: "main" });
      if (result.ok) {
        due.forEach(function (item) {
          dispatched[item.token] = true;
          dispatchedLabels.push(item.label + "(" + item.key + ")");
        });
        saveDispatched_(dispatched);
        Logger.log("clock: dispatched scheduler for " + dispatchedLabels.join(", "));
      } else {
        errors.push("scheduler dispatch HTTP " + result.code + ": " + result.body);
      }
    } else {
      Logger.log("clock: no dispatches due");
    }
  } catch (err) {
    errors.push(String(err));
  }

  var body =
    "appscript clock " +
    (errors.length ? "ERROR: " + errors.join(" | ") : "ok") +
    " due=" +
    (dueLabels.join(",") || "none") +
    " dispatched=" +
    (dispatchedLabels.join(",") || "none");
  Logger.log(body);
  pingClock_(body, errors.length === 0);
}

function onSheetChange(e) {
  if (!e || e.changeType !== "EDIT") {
    return;
  }
  var props = PropertiesService.getScriptProperties();
  var last = Number(props.getProperty("_LAST_REFRESH_MS") || "0");
  var nowMs = Date.now();
  if (nowMs - last < REFRESH_THROTTLE_MS) {
    Logger.log("onChange: throttled (last refresh " + Math.round((nowMs - last) / 1000) + "s ago)");
    return;
  }
  var result = dispatch_(configRefreshUrl_(), { ref: "main" });
  if (result.ok) {
    props.setProperty("_LAST_REFRESH_MS", String(nowMs));
    Logger.log("onChange: dispatched config_refresh");
  } else {
    Logger.log("onChange: config_refresh dispatch HTTP " + result.code + ": " + result.body);
  }
}

function setupTriggers() {
  var props = PropertiesService.getScriptProperties();
  var ss = SpreadsheetApp.openById(sheetId_());
  if (!props.getProperty("SHEET_ID")) {
    props.setProperty("SHEET_ID", ss.getId());
  }
  if (!props.getProperty("SCHEDULER_DISPATCH_URL")) {
    props.setProperty("SCHEDULER_DISPATCH_URL", DEFAULT_SCHEDULER_DISPATCH_URL);
  }
  if (!props.getProperty("CONFIG_REFRESH_DISPATCH_URL")) {
    props.setProperty("CONFIG_REFRESH_DISPATCH_URL", DEFAULT_CONFIG_REFRESH_DISPATCH_URL);
  }

  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    var handler = trigger.getHandlerFunction();
    if (handler === "clockTick" || handler === "onSheetChange") {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  ScriptApp.newTrigger("clockTick").timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger("onSheetChange").forSpreadsheet(ss).onChange().create();
  Logger.log("setupTriggers: clockTick(every 5 min) + onSheetChange(EDIT) installed");
}

function testDispatch() {
  var result = dispatch_(schedulerUrl_(), { ref: "main" });
  Logger.log(
    "testDispatch scheduler: ok=" + result.ok + " code=" + result.code + " body=" + result.body
  );
  if (!result.ok) {
    throw new Error("testDispatch failed: HTTP " + result.code + " " + result.body);
  }
}

function readAjustes_() {
  var id = sheetId_();
  var sheet = SpreadsheetApp.openById(id).getSheetByName("ajustes");
  if (!sheet) {
    throw new Error("ajustes tab not found in sheet " + id);
  }
  var values = sheet.getDataRange().getValues();
  var out = {};
  var start = 0;
  for (var i = 0; i < values.length; i++) {
    var first = String(values[i][0] || "").trim().toLowerCase();
    var second = String(values[i][1] || "").trim().toLowerCase();
    if (first === "clave" && (second === "valor" || second === "value")) {
      start = i + 1;
      break;
    }
  }
  for (var j = start; j < values.length; j++) {
    var key = String(values[j][0] || "").trim();
    if (!key) {
      continue;
    }
    var value = String(values[j][1] || "").trim();
    out[key] = value;
    out[key.toLowerCase()] = value;
  }
  return out;
}

function parseDailyCron_(expression) {
  if (!expression) {
    return null;
  }
  var parts = String(expression).trim().split(/\s+/);
  if (parts.length !== 5) {
    return null;
  }
  if (parts[2] !== "*" || parts[3] !== "*" || parts[4] !== "*") {
    // Only daily `m h * * *` is dispatched by the clock. Other forms still
    // work through the 6 h GitHub backup (scheduler catch-up).
    return null;
  }
  var minute = Number(parts[0]);
  var hour = Number(parts[1]);
  if (!Number.isInteger(minute) || minute < 0 || minute > 59) {
    return null;
  }
  if (!Number.isInteger(hour) || hour < 0 || hour > 23) {
    return null;
  }
  return {
    hour: hour,
    minute: minute,
    label: (hour < 10 ? "0" : "") + hour + ":" + (minute < 10 ? "0" : "") + minute,
  };
}

function withinWindow_(nowMinutes, startMinutes) {
  var delta = (nowMinutes - startMinutes + 24 * 60) % (24 * 60);
  return delta >= 0 && delta < WINDOW_MINUTES;
}

function loadDispatched_(today) {
  var raw = PropertiesService.getScriptProperties().getProperty("_DISPATCHED_WINDOWS");
  var map = {};
  if (raw) {
    try {
      map = JSON.parse(raw);
    } catch (err) {
      map = {};
    }
  }
  var pruned = {};
  Object.keys(map).forEach(function (key) {
    if (key.indexOf(today + "|") === 0) {
      pruned[key] = map[key];
    }
  });
  return pruned;
}

function saveDispatched_(map) {
  PropertiesService.getScriptProperties().setProperty(
    "_DISPATCHED_WINDOWS",
    JSON.stringify(map)
  );
}

function dispatch_(url, payload) {
  var pat = PropertiesService.getScriptProperties().getProperty("GH_PAT");
  if (!pat) {
    return { ok: false, code: 0, body: "GH_PAT missing" };
  }
  if (!url) {
    return { ok: false, code: 0, body: "dispatch URL missing" };
  }
  try {
    var response = UrlFetchApp.fetch(url, {
      method: "post",
      contentType: "application/json",
      headers: {
        Authorization: "Bearer " + pat,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
    });
    var code = response.getResponseCode();
    var body = response.getContentText() || "";
    if (body.length > 300) {
      body = body.slice(0, 300);
    }
    return { ok: code >= 200 && code < 300, code: code, body: body };
  } catch (err) {
    return { ok: false, code: 0, body: String(err) };
  }
}

function pingClock_(body, success) {
  var base = PropertiesService.getScriptProperties().getProperty(
    "APPSCRIPT_HEALTHCHECKS_URL"
  );
  if (!base) {
    Logger.log("clock: APPSCRIPT_HEALTHCHECKS_URL missing; liveness ping skipped");
    return;
  }
  var url = String(base).replace(/\/+$/, "") + (success ? "" : "/fail");
  try {
    UrlFetchApp.fetch(url, { method: "post", payload: body, muteHttpExceptions: true });
  } catch (err) {
    Logger.log("clock: liveness ping failed: " + err);
  }
}

function schedulerUrl_() {
  return (
    PropertiesService.getScriptProperties().getProperty("SCHEDULER_DISPATCH_URL") ||
    DEFAULT_SCHEDULER_DISPATCH_URL
  );
}

function configRefreshUrl_() {
  return (
    PropertiesService.getScriptProperties().getProperty("CONFIG_REFRESH_DISPATCH_URL") ||
    DEFAULT_CONFIG_REFRESH_DISPATCH_URL
  );
}

function sheetId_() {
  var configured = PropertiesService.getScriptProperties().getProperty("SHEET_ID");
  if (configured) {
    return configured;
  }
  return SpreadsheetApp.getActiveSpreadsheet().getId();
}
