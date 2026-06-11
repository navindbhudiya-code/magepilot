<?php
declare(strict_types=1);

namespace Vendor\Faq\Plugin;

use Vendor\Faq\Model\FaqRepository;

class BrokenPlugin
{
    /**
     * Typo on purpose: FaqRepository has no fetchAll() method — diagnose_plugin
     * must flag this as a method-name mismatch.
     */
    public function afterFetchAll(FaqRepository $subject, $result)
    {
        return $result;
    }
}
