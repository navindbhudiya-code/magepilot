# Magento 2 Extension Mechanisms: Plugin vs Preference vs Observer

## What a preference is (and is NOT)
A **preference** is a dependency-injection directive in `di.xml` that **replaces one class with another**.
It is **NOT** a setting, **NOT** a configuration value, and has **nothing to do with user/store preferences**.

```xml
<preference for="Vendor\Module\Api\ThingInterface" type="Vendor\Module\Model\Thing"/>
```

It means: "everywhere `ThingInterface` is requested, instantiate `Thing` instead." Two legitimate uses:
1. Bind your own interface to its concrete implementation (the normal, recommended case).
2. Replace a core/third-party class wholesale (use sparingly — only **one** preference wins per type, so
   two modules overriding the same class conflict).

Admin-editable **settings** are a completely different thing: they live in `system.xml` (the form) and
`config.xml` (defaults), are stored in `core_config_data`, and are read with `ScopeConfigInterface`.

## What a plugin (interceptor) is
A **plugin** intercepts a **public** method of a class to run code `before`, `after`, or `around` it,
**without replacing** the class. Declared in `di.xml`:

```xml
<type name="Magento\Catalog\Model\Product">
    <plugin name="vendor_suffix" type="Vendor\Module\Plugin\AppendSuffix"/>
</type>
```

- `beforeMethod($subject, ...$args)` — validate/normalize arguments; return an array of args (or null).
- `afterMethod($subject, $result, ...$args)` — transform the return value (most common, cheapest).
- `aroundMethod($subject, callable $proceed, ...$args)` — wrap the call; can conditionally skip the
  original via not calling `$proceed` (use sparingly — heaviest, easy to break the chain).

Plugins are composable (many modules can plug the same method, ordered by `sortOrder`). They only work on
**public, non-final, non-static** methods, and do **not** intercept internal `$this->method()` self-calls
or objects created with `new`. After changing plugins, run `setup:di:compile`.

## What an observer is
An **observer** reacts to an **event** that Magento (or your code) dispatches. Declared in `events.xml`,
implements `ObserverInterface::execute(Observer $observer)`.

```xml
<event name="catalog_product_save_after">
    <observer name="vendor_x" instance="Vendor\Module\Observer\X"/>
</event>
```

Observers are for **side effects** (notifications, sync, logging) in response to events. They cannot
reliably change a method's return value, and only fire where an event already exists.

## When to use which (decision rule)
- **Plugin** — change the behavior, arguments, or return value of a specific public method. The
  upgrade-safe way to tweak core/third-party behavior.
- **Preference** — bind your own interface to its implementation, or (rarely) replace an entire class.
  Only one preference per type wins, so they collide; prefer a plugin for behavior changes.
- **Observer** — react to an event with a side effect, when an event exists at that point.

Summary: **preference = class substitution; plugin = method interception; observer = event reaction;
configuration (system.xml + ScopeConfig) = settings.**
