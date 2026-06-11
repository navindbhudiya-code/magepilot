<?php
declare(strict_types=1);

namespace Vendor\Faq\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;

class InvalidateCache implements ObserverInterface
{
    public function execute(Observer $observer): void
    {
        // invalidate the FAQ cache tag
    }
}
