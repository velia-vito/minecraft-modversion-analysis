# Modrinth Modpack Builder

A Python tool that searches [Modrinth](https://modrinth.com/) for Minecraft mods, finds the best common Minecraft version they all support, resolves dependencies, detects conflicts, and optionally downloads all the jar files — all from one command.

---

## Table of Contents

1. [What Does This Do?](#what-does-this-do)
2. [Installing Python (First-Time Setup)](#installing-python-first-time-setup)
3. [Downloading This Tool](#downloading-this-tool)
4. [Installing Dependencies](#installing-dependencies)
5. [Setting Up Your Mod List](#setting-up-your-mod-list)
6. [Running the Script](#running-the-script)
7. [Understanding the Output](#understanding-the-output)
8. [Downloading Mods](#downloading-mods)
9. [Command-Line Options](#command-line-options)
10. [Config File Reference](#config-file-reference)
11. [Troubleshooting](#troubleshooting)
12. [How It Works](#how-it-works)
13. [Files in This Project](#files-in-this-project)

---

## What Does This Do?

Imagine you want to play Minecraft with 50+ mods. You need to figure out:

- Which Minecraft version do **all** these mods support?
- What extra mods (dependencies) do they need?
- Are there any conflicts between mod versions?
- Where do you download all the `.jar` files?

This tool answers all of those questions automatically. You give it a list of mod names, and it:

1. **Searches Modrinth** for each mod
2. **Finds dependencies** (mods that other mods require)
3. **Calculates the best Minecraft version** that supports the most mods
4. **Detects conflicts** (e.g., two mods needing different versions of the same library)
5. **Downloads all the mod files** if you want

---

## Installing Python (First-Time Setup)

Python is the programming language this tool is written in. You need to install it once.

### Step 1: Download Python

1. Open your web browser and go to: **https://www.python.org/downloads/**
2. Click the big yellow **"Download Python 3.x.x"** button (the exact number doesn't matter as long as it's 3.8 or higher)

### Step 2: Run the Installer

1. Open the downloaded file (it will be named something like `python-3.12.x-amd64.exe`)
2. **IMPORTANT:** At the bottom of the installer window, check the box that says:
   > ☑ **Add python.exe to PATH**
   
   This is critical — without it, you won't be able to run Python from the command line.
3. Click **"Install Now"**
4. Wait for installation to complete, then click **"Close"**

### Step 3: Verify Python Works

1. Press `Win + R` on your keyboard, type `cmd`, and press Enter (this opens Command Prompt)
2. Type the following and press Enter:
   ```
   python --version
   ```
3. You should see something like `Python 3.12.4`. If you see an error instead, restart your computer and try again. If it still doesn't work, re-run the Python installer and make sure "Add to PATH" is checked.

---

## Downloading This Tool

### Option A: Download as ZIP

1. On the GitHub page for this project, click the green **"Code"** button
2. Click **"Download ZIP"**
3. Extract the ZIP file to a folder you can find easily, for example:
   ```
   C:\Users\YourName\Desktop\modpack-builder
   ```

### Option B: Using Git (if you have it)

```
git clone <repository-url>
cd tester-script
```

---

## Installing Dependencies

This tool needs one extra Python package called `requests` (for making web requests to Modrinth).

### Step 1: Open a Terminal in the Tool's Folder

1. Open File Explorer and navigate to the folder where you extracted the tool
2. Click in the address bar at the top, type `cmd`, and press Enter
   - This opens a Command Prompt in that folder
   - Alternatively: hold Shift, right-click in the folder, and select "Open PowerShell window here"

### Step 2: Install Requirements

Type this command and press Enter:

```
pip install -r requirements.txt
```

You should see output like:
```
Successfully installed requests-2.31.0 tqdm-4.66.1
```

If you see `'pip' is not recognized`, try:
```
python -m pip install -r requirements.txt
```

### Alternative: Use setup.bat

Double-click `setup.bat` in the folder — it checks for Python and installs dependencies automatically.

---

## Setting Up Your Mod List

The tool reads a file called `config.json` that contains your list of mods. A sample is already included.

### Editing config.json

Open `config.json` in any text editor (Notepad works fine — right-click the file → "Open with" → Notepad).

Here's what it looks like:

```json
{
  "loader": "fabric",
  "mods": [
    "Sodium",
    "Lithium",
    "Iris Shaders",
    "Jade"
  ],
  "ignore": [],
  "skip-results": []
}
```

### Fields Explained

| Field | What it does | Example |
|---|---|---|
| `loader` | Your mod loader. Use `"fabric"`, `"forge"`, `"quilt"`, or `"neoforge"` | `"fabric"` |
| `mods` | List of mod names to search for. Spelling doesn't need to be exact — the tool uses fuzzy matching | `["Sodium", "Better combat", "YUNG's Better Dungeons"]` |
| `ignore` | Mods that should be found but excluded from the final list (e.g., mods you'll add manually) | `["CompleteConfig"]` |
| `skip-results` | Mod names to filter out from search results (if a wrong mod keeps appearing) | `[]` |

### Tips for Mod Names

- **Case doesn't matter**: `"sodium"`, `"Sodium"`, and `"SODIUM"` all work
- **Partial names work**: `"betterf3"` will find "BetterF3"
- **Slugs work**: You can use the URL slug from Modrinth (e.g., `"sodium"` from `modrinth.com/mod/sodium`)
- **Put each mod on its own line** between quotes, separated by commas
- **The last mod in the list should NOT have a comma after it**

### Example: Large Mod List

```json
{
  "loader": "fabric",
  "mods": [
    "Sodium",
    "Lithium",
    "Iris Shaders",
    "Distant Horizons",
    "ferritecore",
    "entity culling",
    "mod menu",
    "Jade",
    "emi",
    "Farmer's Delight (Fabric port)"
  ],
  "ignore": [],
  "skip-results": []
}
```

---

## Running the Script

### Step 1: Open a Terminal

Open Command Prompt or PowerShell in the tool's folder (same as the "Installing Dependencies" step).

### Step 2: Run the Command

```
python modrinth_finder_v2.py config.json
```

### What Happens

The script will:

1. Show how many threads it's using (automatically uses all your CPU cores)
2. **Phase 1** — Search Modrinth for all your mods in parallel
3. **Phase 2** — Fetch version information for each found mod
4. **Phase 3** — Discover and resolve dependencies
5. **Analysis** — Find the best Minecraft version and check for conflicts
6. Display the results in formatted tables
7. Ask what you'd like to do next (download, export, or exit)

A typical run with 73 mods takes **20–45 seconds** on a modern computer.

---

## Understanding the Output

### Search Phase

```
SEARCHING 73 MODS — LOADER: FABRIC
  Workers: 16 threads (cpu_count=16)
  Searching... 73/73
  ✗ "TrueDarkness" — exists but NOT available for fabric
  ✓ [MOD] "Sodium"
                   Sodium
                   https://modrinth.com/mod/sodium
  Phase 1 (search): 5.1s — 71 found, 2 not found
```

- **✓** = mod found successfully
- **✗** = mod not found or not available for your loader
- **⚠** = mod found but match quality is uncertain (check the link)

### Results

```
✓ Common version: 1.21.1

──────────────────────────────────────────────────────
  MC 1.21.1  —  70/70 compatible
──────────────────────────────────────────────────────
```

This means all 70 of your active mods support Minecraft 1.21.1.

### Install Plan Table

Shows every mod, its version, and a link:

```
┌──────────────────────┬──────────────┬─────────────────────────────────┐
│ Mod                  │ Version      │ Link                            │
├──────────────────────┼──────────────┼─────────────────────────────────┤
│ Sodium               │ 0.6.13       │ https://modrinth.com/mod/sodium │
│   └─ Fabric API      │ 0.116.9      │ https://modrinth.com/mod/...    │
└──────────────────────┴──────────────┴─────────────────────────────────┘
```

- Indented rows (with `└─`) are **dependencies** — mods required by the parent mod
- Rows with **→** show alternative projects found automatically (e.g., version-specific forks)

### Conflict Warnings

```
⚠ CONFLICTS (2):
┌───┬────────────────┬──────────────────────────┬─────────┐
│ # │ Mod            │ Issue                    │ Link    │
├───┼────────────────┼──────────────────────────┼─────────┤
│ 1 │ Some Mod       │ needs LibX 1.2 (sel 1.3) │ ...     │
└───┴────────────────┴──────────────────────────┴─────────┘
```

This means some mods need different versions of the same dependency. You may need to manually pick compatible versions.

---

## Downloading Mods

### Interactive (Recommended)

After results are shown, the script asks:

```
What would you like to do?
  [d] Download mods
  [o] Export install plan as JSON
  [Enter] Done — exit
```

Press `d` and Enter. It will ask:
- **Output directory** — where to save files (default: `mods`)
- **Split into client/server folders?** — answer `y` if you want separate folders
- **Ambiguous mods go to** — `client` or `server` (for mods that work on both sides)

### Command Line

Download directly without prompts:

```
python modrinth_finder_v2.py config.json -d mods
```

With client/server split:

```
python modrinth_finder_v2.py config.json -d mods --split --bias server
```

### Export JSON Only (No Download)

```
python modrinth_finder_v2.py config.json -o modslist.json
```

This saves a JSON file with all mod details (name, version, URL, dependencies) that you can use for reference or share with others.

---

## Command-Line Options

```
python modrinth_finder_v2.py config.json [options]
```

| Option | Description | Example |
|---|---|---|
| `config.json` | **(Required)** Path to your config file | `config.json` |
| `-o FILE` | Export install plan as JSON to FILE | `-o modslist.json` |
| `-d DIR` | Download mod jars to DIR | `-d mods` |
| `--split` | Split downloads into `client/` and `server/` subfolders | `--split` |
| `--bias` | Which side gets ambiguous mods: `client` (default) or `server` | `--bias server` |

### Examples

```bash
# Just search and show results (interactive)
python modrinth_finder_v2.py config.json

# Search and export JSON
python modrinth_finder_v2.py config.json -o modslist.json

# Search and download everything
python modrinth_finder_v2.py config.json -d mods

# Search, download, split by side
python modrinth_finder_v2.py config.json -d mods --split --bias client

# Export AND download
python modrinth_finder_v2.py config.json -o modslist.json -d mods
```

---

## Config File Reference

### Minimal Config

```json
{
  "loader": "fabric",
  "mods": ["Sodium", "Lithium"]
}
```

### Full Config

```json
{
  "loader": "fabric",
  "mods": [
    "Sodium",
    "Lithium",
    "Iris Shaders",
    "Jade"
  ],
  "ignore": [
    "CompleteConfig"
  ],
  "skip-results": []
}
```

### Field Details

**`loader`** (required)
- Which mod loader you use
- Valid values: `"fabric"`, `"forge"`, `"quilt"`, `"neoforge"`

**`mods`** (required)
- Array of mod names to search
- The tool sorts this list alphabetically and saves it back (keeps your config tidy)
- Names are matched against Modrinth using multi-tier fuzzy matching:
  1. Exact slug match
  2. Exact title match
  3. Normalised slug match
  4. Title prefix match
  5. Title contains match
  6. Best available fallback

**`ignore`** (optional, default: `[]`)
- Mods to find but exclude from compatibility analysis
- Useful for library mods or mods you handle separately
- These mods are still searched and shown, but marked as ignored

**`skip-results`** (optional, default: `[]`)
- Filter out specific mod titles from search results
- Useful when a search query returns a wrong mod with a similar name

---

## Troubleshooting

### "python is not recognized"

Python isn't installed or isn't in your PATH.

**Fix:** Re-install Python from https://www.python.org/downloads/ and make sure to check **"Add python.exe to PATH"** during installation. Restart your terminal after installing.

### "pip is not recognized"

**Fix:** Use `python -m pip install -r requirements.txt` instead of `pip install ...`

### "No module named 'requests'"

The dependencies aren't installed.

**Fix:** Run `pip install -r requirements.txt` or `python -m pip install requests`

### Mod not found

- Check the spelling — try the exact name from the Modrinth website
- Try the mod's slug (the part after `modrinth.com/mod/` in the URL)
- The mod might not exist on Modrinth (some mods are only on CurseForge)

### "exists but NOT available for fabric"

The mod exists on Modrinth but doesn't support your loader. You'll need to:
- Find an alternative mod that does the same thing
- Switch to a different loader
- Remove it from your list

### Script is slow

- First run is slower due to API cold start
- Subsequent runs benefit from HTTP connection reuse
- The script automatically uses all CPU cores for parallelism
- Modrinth rate-limits at ~300 requests/minute — the retry adapter handles this automatically

### Download failures

- Check your internet connection
- The retry adapter makes 5 attempts with exponential backoff
- If a specific mod keeps failing, try downloading it manually from the URL shown

### JSON syntax errors in config.json

Common mistakes:
- Missing comma between items: `"Sodium" "Lithium"` → `"Sodium", "Lithium"`
- Comma after last item: `"Lithium",` → `"Lithium"` (no trailing comma)
- Missing quotes: `Sodium` → `"Sodium"`
- Wrong brackets: `( )` → `[ ]`

Use a JSON validator like https://jsonlint.com/ to check your config.

---

## How It Works

### Architecture

The script runs in 4 phases, all using maximum parallelism:

1. **Phase 1 — Parallel Search** (`os.cpu_count()` threads)
   - Searches Modrinth for every mod in your list simultaneously
   - Uses a 6-tier matching algorithm to find the best match
   - Detects wrong-loader mods and reports them
   - Automatic retry with exponential backoff on rate limits (429)

2. **Phase 2 — Parallel Version Fetch** (`os.cpu_count()` threads)
   - Fetches all available versions for each found mod
   - Fetches project metadata (client/server side info)
   - Collects dependency project IDs for Phase 3

3. **Phase 3 — BFS Dependency Resolution** (bulk API + parallel)
   - Uses breadth-first search to discover all transitive dependencies
   - Bulk-fetches project metadata using `/projects?ids=[...]`
   - Parallel version fetching for dependency waves

4. **Analysis** (parallel per MC version)
   - Evaluates top 5 MC version candidates concurrently
   - For each version: builds install plan, searches for alt-projects in parallel, detects conflicts
   - Selects the best version (most mods compatible, latest MC)

### API Usage

- Endpoint: `https://api.modrinth.com/v2`
- No API key required
- Rate limit: ~300 requests/minute (handled automatically)
- Retry: 5 attempts with exponential backoff (0.6s, 1.2s, 2.4s, 4.8s, 9.6s)
- Connection pool sized to CPU core count for maximum throughput

---

## Files in This Project

| File | Description |
|---|---|
| `modrinth_finder_v2.py` | **Main script** — use this one. Search, analyse, and download mods. |
| `modrinth_finder.py` | Original v1 script — kept for reference. Slower (sequential). |
| `config.json` | Your mod list and settings. Edit this file. |
| `requirements.txt` | Python package dependencies (`requests`, `tqdm`). |
| `setup.bat` | Windows helper — checks Python and installs dependencies. |
| `CHANGELOG.md` | Version history with performance comparisons. |
| `README.md` | This file. |

---

## License

MIT
