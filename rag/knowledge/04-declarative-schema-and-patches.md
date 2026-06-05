# Magento 2 Database: Declarative Schema, Data Patches, Schema Patches

## Declarative schema (db_schema.xml) — the default for structure
Describe the **desired end state** of tables in `etc/db_schema.xml`; Magento diffs current vs target and
applies the delta. It replaced `InstallSchema`/`UpgradeSchema` (and `module.xml` no longer has
`setup_version`).

```xml
<schema xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="urn:magento:framework:Setup/Declaration/Schema/etc/schema.xsd">
    <table name="vendor_module_record" resource="default" engine="innodb" comment="Records">
        <column xsi:type="int" name="entity_id" unsigned="true" nullable="false" identity="true"/>
        <column xsi:type="varchar" name="title" length="255" nullable="false"/>
        <column xsi:type="smallint" name="status" unsigned="true" nullable="false" default="1"/>
        <constraint xsi:type="primary" referenceId="PRIMARY"><column name="entity_id"/></constraint>
        <index referenceId="VENDOR_MODULE_RECORD_STATUS" indexType="btree"><column name="status"/></index>
    </table>
</schema>
```
Notes: use `decimal` (with `scale`/`precision`) for money, never `float`. Foreign keys use
`<constraint xsi:type="foreign" ... onDelete="CASCADE|SET NULL"/>` (SET NULL requires a nullable column).
Rename non-destructively with `onCreate="migrateDataFrom(old_col)"`.

## db_schema_whitelist.json (the drop guardrail)
Declarative schema only **drops** elements that are in `db_schema_whitelist.json`. Regenerate it whenever
you change `db_schema.xml`, and commit both together:
```
bin/magento setup:db-declaration:generate-whitelist --module-name=Vendor_Module
bin/magento setup:upgrade
```
Skipping it: additions still apply, but removals are ignored and some versions error during upgrade.

## Data patches (one-off data changes)
`Setup/Patch/Data/*.php` implementing `DataPatchInterface`. Runs **once** (tracked in `patch_list`).
Use for seeding rows, adding EAV attributes, backfilling values. Make the body **idempotent**
(e.g. `insertOnDuplicate`). Ordering is via `getDependencies()` (no version numbers); `getAliases()`
lets you rename a patch class without it re-running. Implement `PatchRevertableInterface::revert()` for
clean uninstall. An applied patch will NOT re-run if you edit it — write a new patch instead.

## Schema patches (rare, imperative DDL)
`Setup/Patch/Schema/*.php` implementing `SchemaPatchInterface` — only for imperative DDL that declarative
schema cannot express (e.g. data transformations interleaved with an alter). Prefer declarative schema.

## Which to use
- Structural DB changes (tables/columns/indexes/FKs) → **declarative schema**.
- One-off data changes (seed/backfill/EAV attribute) → **data patch**.
- Rare imperative DDL declarative schema can't express → **schema patch**.
Never use the removed `InstallSchema`/`UpgradeSchema`/`InstallData`/`UpgradeData` scripts.
